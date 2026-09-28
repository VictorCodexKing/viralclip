"""Word-timeline helpers used by captions and the pipeline.

Adapted from the reference ``media/timeline.py`` and ``clip_source_map.py``,
trimmed to what the local pipeline needs: source-range normalisation, timestamp
parsing/formatting, word-in-range projection for captions, and a light
sentence-boundary extension. The reference's filler-word/pause "cleanup" layer
is intentionally dropped for this simplified build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import (
    CLIP_END_PADDING_SECONDS,
    CLIP_END_SENTENCE_EXTENSION_SECONDS,
    SENTENCE_END_RE,
    logger,
)
from .ffmpeg import ffprobe_duration
from .transcription import _join_transcript_tokens, load_cached_transcript_data


def normalize_source_ranges(
    ranges: Optional[List[Tuple[float, float]]],
) -> List[Tuple[float, float]]:
    """Sort, clamp, and merge overlapping (start, end) second ranges."""
    if not ranges:
        return []
    cleaned: List[Tuple[float, float]] = []
    for item in ranges:
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            cleaned.append((start, end))
    if not cleaned:
        return []
    cleaned.sort()
    merged: List[Tuple[float, float]] = [cleaned[0]]
    for start, end in cleaned[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def parse_timestamp_to_seconds(timestamp_str: str) -> float:
    """Parse ``MM:SS``/``HH:MM:SS``/plain-seconds to a float second count."""
    try:
        timestamp_str = str(timestamp_str).strip()
        if ":" in timestamp_str:
            parts = timestamp_str.split(":")
            if len(parts) == 2:
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
        return float(timestamp_str)
    except (ValueError, IndexError) as exc:
        logger.error("Failed to parse timestamp '%s': %s", timestamp_str, exc)
        return 0.0


def seconds_to_mmss(seconds: float) -> str:
    """Format seconds as ``MM:SS`` with integer-second precision."""
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def word_ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END_RE.search((text or "").strip()))


def get_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict]:
    """Extract words overlapping a clip timerange, re-based to clip-local time."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words = []
    for word_data in transcript_data["words"]:
        word_start = word_data["start"]
        word_end = word_data["end"]
        if word_start < clip_end_ms and word_end > clip_start_ms:
            relative_start = max(0, (word_start - clip_start_ms) / 1000.0)
            relative_end = min(
                (clip_end_ms - clip_start_ms) / 1000.0,
                (word_end - clip_start_ms) / 1000.0,
            )
            if relative_end > relative_start:
                relevant_words.append(
                    {
                        "text": word_data["text"],
                        "start": relative_start,
                        "end": relative_end,
                        "confidence": word_data.get("confidence", 1.0),
                    }
                )
    return relevant_words


def get_absolute_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict[str, Any]]:
    """Extract words overlapping a range, keeping absolute (source) timing."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words: List[Dict[str, Any]] = []
    for word_data in transcript_data["words"]:
        word_start = int(word_data["start"])
        word_end = int(word_data["end"])
        overlap_start = max(word_start, clip_start_ms)
        overlap_end = min(word_end, clip_end_ms)
        if overlap_end <= overlap_start:
            continue
        relevant_words.append(
            {
                "text": word_data["text"],
                "start": overlap_start / 1000.0,
                "end": overlap_end / 1000.0,
                "confidence": word_data.get("confidence", 1.0),
            }
        )
    return relevant_words


def get_words_for_keep_ranges(
    transcript_data: Dict, keep_ranges: List[Tuple[float, float]]
) -> List[Dict[str, Any]]:
    """Project word timings into the output timeline for a set of keep ranges."""
    if not transcript_data or not transcript_data.get("words") or not keep_ranges:
        return []

    relevant_words: List[Dict[str, Any]] = []
    timeline_offset = 0.0
    for keep_start, keep_end in normalize_source_ranges(keep_ranges):
        for word in get_absolute_words_in_range(transcript_data, keep_start, keep_end):
            relevant_words.append(
                {
                    "text": word["text"],
                    "start": timeline_offset + (word["start"] - keep_start),
                    "end": timeline_offset + (word["end"] - keep_start),
                    "confidence": word.get("confidence", 1.0),
                }
            )
        timeline_offset += keep_end - keep_start
    return relevant_words


def get_transcript_text_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> str:
    """Return transcript text reconstructed from cached word timings."""
    relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words:
        return ""
    return _join_transcript_tokens([w["text"] for w in relevant_words])


def extend_keep_ranges_to_sentence_boundary(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    max_extension_seconds: float = CLIP_END_SENTENCE_EXTENSION_SECONDS,
    padding_seconds: float = CLIP_END_PADDING_SECONDS,
) -> List[Tuple[float, float]]:
    """Extend the final source range when the clip end lands mid-sentence."""
    normalized = normalize_source_ranges(keep_ranges)
    if not normalized:
        return []

    last_start, last_end = normalized[-1]
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return normalized

    try:
        source_duration = ffprobe_duration(video_path)
    except Exception:
        source_duration = None

    cap_end = last_end + max(0.0, max_extension_seconds)
    if source_duration is not None:
        cap_end = min(cap_end, source_duration)
    if cap_end <= last_end:
        return normalized

    nearby_words = get_absolute_words_in_range(
        transcript_data, max(0.0, last_end - 6.0), cap_end
    )
    if not nearby_words:
        return normalized

    boundary_words = [w for w in nearby_words if float(w["start"]) <= last_end + 0.05]
    last_boundary_word = boundary_words[-1] if boundary_words else None
    if (
        last_boundary_word
        and float(last_boundary_word["end"]) <= last_end + 0.05
        and word_ends_sentence(str(last_boundary_word.get("text", "")))
    ):
        return normalized

    extended_end = last_end
    for word in nearby_words:
        word_end = float(word["end"])
        if word_end <= last_end + 0.05:
            continue
        extended_end = max(extended_end, word_end)
        if word_ends_sentence(str(word.get("text", ""))):
            extended_end += max(0.0, padding_seconds)
            break

    if extended_end <= last_end:
        return normalized
    if source_duration is not None:
        extended_end = min(extended_end, source_duration)
    extended_end = min(extended_end, cap_end + max(0.0, padding_seconds))
    if extended_end - last_start <= 0.05:
        return normalized
    return [*normalized[:-1], (last_start, extended_end)]

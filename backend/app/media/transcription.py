"""Transcription helpers for the video pipeline.

Adapted from the reference ``media/transcription.py``. Defaults to **local
Whisper** with word-level timestamps so no AssemblyAI key is required. The
AssemblyAI provider is used only when ``ASSEMBLY_AI_API_KEY`` is set (and
``TRANSCRIPTION_PROVIDER=assemblyai``); a YouTube-captions provider is also
available for plain-text transcripts.

All providers normalise into the transcript-cache sidecar
``{version, words:[{text,start,end,confidence,speaker}], utterances, text}``
(times in milliseconds) and ``format_transcript_for_analysis`` produces
``[MM:SS - MM:SS] text`` lines for the LLM.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import get_config
from .common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    logger,
)
from .ffmpeg import run_ffmpeg_command

try:  # optional local backend; only required for the whisper provider
    import whisper as _whisper

    _WHISPER_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _whisper = None
    _WHISPER_AVAILABLE = False

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}


# --- Audio prep ---------------------------------------------------------------


def _prepare_audio_for_transcription(video_path: Path) -> Path:
    """Extract a compact mono 16 kHz audio file for transcription."""
    audio_path = video_path.with_name(f"{video_path.stem}.transcription.mp3")
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning("ffmpeg not available; using source video for transcription")
        return video_path

    if (
        result.returncode != 0
        or not audio_path.exists()
        or audio_path.stat().st_size == 0
    ):
        logger.warning("Audio extraction failed; using source video for transcription")
        return video_path
    return audio_path


# --- Whisper ------------------------------------------------------------------


def _get_whisper_model(model_name: str = "base"):
    """Load and cache a Whisper model by name."""
    if not _WHISPER_AVAILABLE:
        raise RuntimeError(
            "Whisper is not installed. Install it with: uv add openai-whisper"
        )
    if model_name not in _WHISPER_MODEL_CACHE:
        logger.info("Loading Whisper model: %s", model_name)
        _WHISPER_MODEL_CACHE[model_name] = _whisper.load_model(model_name)
    return _WHISPER_MODEL_CACHE[model_name]


def transcribe_with_whisper(
    video_path: Path, model_name: str = "base"
) -> Dict[str, Any]:
    """Transcribe a video using local Whisper with word-level timestamps."""
    audio_path = _prepare_audio_for_transcription(video_path)
    model = _get_whisper_model(model_name)
    logger.info("Starting Whisper transcription with model: %s", model_name)
    return model.transcribe(str(audio_path), word_timestamps=True, language=None)


def _whisper_result_to_transcript_data(
    whisper_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Convert a Whisper result dict to the standard transcript-cache format.

    Whisper reports timings in seconds; the cache stores milliseconds, so each
    timestamp is multiplied by 1000 here.
    """
    words_data: List[Dict[str, Any]] = []
    utterances_data: List[Dict[str, Any]] = []

    for segment in whisper_result.get("segments") or []:
        seg_words = [
            {
                "text": w.get("word", w.get("text", "")),
                "start": int(w["start"] * 1000)
                if isinstance(w.get("start"), float)
                else int(w.get("start", 0)),
                "end": int(w["end"] * 1000)
                if isinstance(w.get("end"), float)
                else int(w.get("end", 0)),
                "confidence": w.get("probability", w.get("confidence", 1.0)),
                "speaker": None,
            }
            for w in segment.get("words") or []
        ]
        utterances_data.append(
            {
                "text": segment.get("text", ""),
                "start": int(segment["start"] * 1000) if "start" in segment else 0,
                "end": int(segment["end"] * 1000) if "end" in segment else 0,
                "speaker": None,
                "words": seg_words,
            }
        )
        words_data.extend(seg_words)

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": whisper_result.get("text", ""),
    }


# --- YouTube captions ---------------------------------------------------------


def transcribe_with_youtube_captions(video_url: str) -> Optional[str]:
    """Extract a plain-text transcript from a YouTube video's captions.

    Returns plain text without word-level timings, so word-synced subtitles are
    not available on this path.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error("yt-dlp is required for YouTube caption extraction")
        return None

    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", video_url)
    video_id = match.group(1) if match else None
    if not video_id:
        logger.error("Could not extract YouTube video ID from URL: %s", video_url)
        return None

    temp_dir = Path(get_config().temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    subs_path = temp_dir / f"{video_id}.en.vtt"

    try:
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en"],
            "subtitlesformat": "vtt",
            "skip_download": True,
            "outtmpl": str(temp_dir / video_id),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        if subs_path.exists():
            text = subs_path.read_text(encoding="utf-8")
            lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("WEBVTT")
                    and not stripped.startswith("Kind:")
                    and not stripped.startswith("Language:")
                    and "-->" not in line
                    and not stripped.startswith("NOTE")
                    and not re.match(r"^\d+$", stripped)
                ):
                    lines.append(stripped)
            return " ".join(lines)

        logger.warning("No English captions found for video %s", video_id)
        return None
    except Exception as exc:
        logger.error("Failed to extract YouTube captions: %s", exc)
        return None
    finally:
        for f in temp_dir.glob(f"{video_id}.*"):
            if f.suffix in (".vtt", ".srt", ".ttml", ".json"):
                try:
                    f.unlink()
                except OSError:
                    pass


# --- AssemblyAI ---------------------------------------------------------------


def _transcribe_with_assemblyai(video_path: Path, runtime_config) -> Any:
    """Submit audio to AssemblyAI and return the completed transcript object."""
    import assemblyai as aai

    aai.settings.api_key = runtime_config.assembly_ai_api_key
    transcriber = aai.Transcriber()
    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
    )
    media_path = _prepare_audio_for_transcription(video_path)
    logger.info("Starting AssemblyAI transcription")
    transcript = transcriber.transcribe(str(media_path), config=config_obj)
    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")
    return transcript


# --- Public dispatch ----------------------------------------------------------


def get_video_transcript(
    video_path: Path,
    speech_model: str = "universal",
    source_url: Optional[str] = None,
) -> str:
    """Get a video transcript using the configured provider.

    Dispatches to local Whisper (default), AssemblyAI, or YouTube captions based
    on ``TRANSCRIPTION_PROVIDER``. ``source_url`` enables the youtube_captions
    provider, which needs the original URL rather than a local file path.
    """
    logger.info("Getting transcript for: %s", video_path)
    runtime_config = get_config()
    provider = runtime_config.transcription_provider

    if provider == "youtube_captions":
        if not source_url:
            raise ValueError(
                "youtube_captions provider requires a YouTube URL. "
                "Pass source_url to get_video_transcript()."
            )
        transcript = transcribe_with_youtube_captions(source_url)
        if not transcript:
            raise RuntimeError("YouTube caption extraction failed or returned nothing.")
        logger.info("YouTube caption transcript: %d chars", len(transcript))
        return transcript

    if provider == "assemblyai":
        if not runtime_config.assembly_ai_api_key:
            raise RuntimeError(
                "TRANSCRIPTION_PROVIDER=assemblyai but ASSEMBLY_AI_API_KEY is not set."
            )
        transcript_obj = _transcribe_with_assemblyai(video_path, runtime_config)
        formatted_lines = format_transcript_for_analysis(transcript_obj)
        cache_transcript_data(video_path, transcript_obj)
        result = "\n".join(formatted_lines)
        logger.info("AssemblyAI transcript: %d segments", len(formatted_lines))
        return result

    # Default: local Whisper.
    model_name = runtime_config.whisper_model
    whisper_result = transcribe_with_whisper(video_path, model_name)
    formatted_lines = format_transcript_for_analysis(whisper_result)
    cache_transcript_data(video_path, whisper_result)
    result = "\n".join(formatted_lines)
    logger.info("Whisper transcript: %d segments", len(formatted_lines))
    return result


# --- Cache read/write ---------------------------------------------------------


def _serialize_transcript_word(word) -> Dict[str, Any]:
    if isinstance(word, dict):
        return {
            "text": word.get("word", word.get("text", "")),
            "start": int(word["start"] * 1000)
            if isinstance(word.get("start"), float)
            else int(word.get("start", 0)),
            "end": int(word["end"] * 1000)
            if isinstance(word.get("end"), float)
            else int(word.get("end", 0)),
            "confidence": word.get("probability", word.get("confidence", 1.0)),
            "speaker": word.get("speaker"),
        }
    return {
        "text": word.text,
        "start": word.start,
        "end": word.end,
        "confidence": getattr(word, "confidence", 1.0),
        "speaker": getattr(word, "speaker", None),
    }


def cache_transcript_data(video_path: Path, transcript) -> None:
    """Cache transcript word timings for subtitle generation.

    Handles both AssemblyAI transcript objects and Whisper result dicts.
    """
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if isinstance(transcript, dict):
        cache_data = _whisper_result_to_transcript_data(transcript)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f)
        logger.info("Cached %d words to %s", len(cache_data["words"]), cache_path)
        return

    words_data = []
    if getattr(transcript, "words", None):
        words_data = [_serialize_transcript_word(w) for w in transcript.words]

    utterances_data = []
    if getattr(transcript, "utterances", None):
        utterances_data = [
            {
                "text": utterance.text,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": getattr(utterance, "speaker", None),
                "words": [
                    _serialize_transcript_word(w)
                    for w in getattr(utterance, "words", []) or []
                ],
            }
            for utterance in transcript.utterances
        ]

    cache_data = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": transcript.text,
    }
    with open(cache_path, "w") as f:
        json.dump(cache_data, f)
    logger.info("Cached %d words to %s", len(words_data), cache_path)


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached transcript word-timing data, or ``None`` when absent."""
    cache_path = video_path.with_suffix(".transcript_cache.json")
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            return payload
    except Exception as exc:
        logger.warning("Failed to load transcript cache: %s", exc)
        return None


# --- Formatting for analysis --------------------------------------------------


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to ``MM:SS``."""
    seconds = int(ms) // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def _join_transcript_tokens(tokens: List[str]) -> str:
    text = " ".join(token.strip() for token in tokens if token and token.strip())
    for before, after in (
        (" ,", ","),
        (" .", "."),
        (" !", "!"),
        (" ?", "?"),
        (" ;", ";"),
        (" :", ":"),
        (" n't", "n't"),
        (" 're", "'re"),
        (" 've", "'ve"),
        (" 'll", "'ll"),
        (" 'd", "'d"),
        (" 'm", "'m"),
        (" 's", "'s"),
    ):
        text = text.replace(before, after)
    return text.strip()


def _format_words_for_analysis(
    words: List[Any],
    speaker: Optional[str] = None,
    *,
    min_words_per_segment: int = ANALYSIS_SEGMENT_MIN_WORDS,
    max_words_per_segment: int = ANALYSIS_SEGMENT_MAX_WORDS,
    max_duration_ms: int = ANALYSIS_SEGMENT_MAX_DURATION_MS,
) -> List[str]:
    if not words:
        return []

    formatted_lines: List[str] = []
    current_words: List[Any] = []
    current_start: Optional[int] = None

    def flush_segment() -> None:
        nonlocal current_words, current_start
        if not current_words:
            return
        start_time = format_ms_to_timestamp(current_words[0].start)
        end_time = format_ms_to_timestamp(current_words[-1].end)
        text = _join_transcript_tokens([w.text for w in current_words])
        if text:
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{text}"
            )
        current_words = []
        current_start = None

    for word in words:
        if current_start is None:
            current_start = word.start
        current_words.append(word)
        duration_ms = word.end - current_start
        segment_word_count = len(current_words)
        ends_sentence = str(word.text).endswith((".", "!", "?"))

        should_flush = False
        if segment_word_count >= max_words_per_segment:
            should_flush = True
        elif ends_sentence and segment_word_count >= min_words_per_segment:
            should_flush = True
        elif duration_ms >= max_duration_ms and segment_word_count >= min_words_per_segment:
            should_flush = True

        if should_flush:
            flush_segment()

    flush_segment()
    return formatted_lines


def format_transcript_for_analysis(transcript) -> List[str]:
    """Format transcripts into ``[MM:SS - MM:SS] text`` lines for AI analysis.

    Handles both Whisper result dicts (segments with second-based timings) and
    AssemblyAI transcript objects (utterances/words with millisecond timings).
    """
    # Whisper result dict: treat each segment as an utterance and convert
    # its second-based timings to milliseconds for the timestamp formatter.
    if isinstance(transcript, dict):
        formatted_lines = []
        for segment in transcript.get("segments") or []:
            start_ms = int(segment.get("start", 0) * 1000)
            end_ms = int(segment.get("end", 0) * 1000)
            formatted_lines.append(
                f"[{format_ms_to_timestamp(start_ms)} - "
                f"{format_ms_to_timestamp(end_ms)}] {segment.get('text', '').strip()}"
            )
        return formatted_lines

    utterances = getattr(transcript, "utterances", None) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            utterance_words = list(getattr(utterance, "words", []) or [])
            utterance_duration = max(0, int(utterance.end) - int(utterance.start))
            if utterance_words and (
                utterance_duration > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS
                or len(utterance_words) > ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS
            ):
                formatted_lines.extend(
                    _format_words_for_analysis(
                        utterance_words,
                        getattr(utterance, "speaker", None),
                        max_words_per_segment=ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
                        max_duration_ms=ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
                    )
                )
                continue
            start_time = format_ms_to_timestamp(utterance.start)
            end_time = format_ms_to_timestamp(utterance.end)
            speaker = getattr(utterance, "speaker", None)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utterance.text}"
            )
        return formatted_lines

    words = getattr(transcript, "words", None) or []
    if not words:
        return []
    return _format_words_for_analysis(words)

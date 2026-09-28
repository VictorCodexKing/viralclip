"""Clip editing helpers: trim / split / merge + platform export presets.

Adapted from the reference ``clip_editor.py`` (trimmed to the trim/split/merge
and preset-export operations). Export presets cover TikTok, Reels, and Shorts,
all rendered at 1080x1920. The richer editor UI wiring is completed in a later
feature; these functions are the building blocks it uses.
"""

from __future__ import annotations

import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List


@dataclass
class ExportPreset:
    name: str
    width: int
    height: int
    video_bitrate: str
    audio_bitrate: str


EXPORT_PRESETS = {
    "tiktok": ExportPreset("tiktok", 1080, 1920, "10M", "192k"),
    "reels": ExportPreset("reels", 1080, 1920, "12M", "192k"),
    "shorts": ExportPreset("shorts", 1080, 1920, "10M", "192k"),
}


def get_export_preset(name: str) -> ExportPreset:
    preset = EXPORT_PRESETS.get(name)
    if not preset:
        raise ValueError(f"Unknown export preset: {name}")
    return preset


def _safe_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}.mp4"


def _run(command: List[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    """Return the duration (seconds) of a media file via ``ffprobe``."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(0.0, float(result.stdout.strip()))


# Backwards-compatible private alias used internally.
_ffprobe_duration = ffprobe_duration


def _double_bitrate(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith("m"):
        return f"{int(float(normalized[:-1]) * 2)}M"
    if normalized.endswith("k"):
        return f"{int(float(normalized[:-1]) * 2)}k"
    return value


def _encode_args(audio_bitrate: str = "256k") -> List[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
    ]


def trim_clip_file(
    input_path: Path, output_dir: Path, start_offset: float, end_offset: float
) -> Path:
    """Trim ``start_offset`` seconds off the front and ``end_offset`` off the end."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("trim")
    duration = _ffprobe_duration(input_path)
    start = max(0.0, start_offset)
    end = min(max(start + 0.1, duration - max(end_offset, 0.0)), duration)
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(input_path),
            "-t",
            f"{end - start:.3f}",
            *_encode_args(),
            str(output_path),
        ]
    )
    return output_path


def split_clip_file(
    input_path: Path, output_dir: Path, split_time: float
) -> tuple[Path, Path]:
    """Split a clip into two parts at ``split_time`` seconds."""
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = _ffprobe_duration(input_path)
    split_at = max(0.2, min(split_time, duration - 0.2))
    first_path = output_dir / _safe_name("split_a")
    second_path = output_dir / _safe_name("split_b")
    _run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-t", f"{split_at:.3f}", *_encode_args(), str(first_path),
        ]
    )
    _run(
        [
            "ffmpeg", "-y", "-ss", f"{split_at:.3f}", "-i", str(input_path),
            "-t", f"{duration - split_at:.3f}", *_encode_args(), str(second_path),
        ]
    )
    return first_path, second_path


def merge_clip_files(paths: Iterable[Path], output_dir: Path) -> Path:
    """Concatenate clips (re-encoded) into a single output file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("merge")
    input_paths = list(paths)
    if not input_paths:
        raise ValueError("No clips provided for merge")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="viralclip_concat_", delete=False
    ) as handle:
        list_path = Path(handle.name)
        for path in input_paths:
            escaped = str(path).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    try:
        _run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_path), *_encode_args(), str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)
    return output_path


def export_with_preset(input_path: Path, output_dir: Path, preset_name: str) -> Path:
    """Re-encode a clip for a platform preset (tiktok / reels / shorts)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = get_export_preset(preset_name)
    output_path = output_dir / _safe_name(preset.name)
    scale_filter = (
        f"scale={preset.width}:{preset.height}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-maxrate",
        preset.video_bitrate,
        "-bufsize",
        _double_bitrate(preset.video_bitrate),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        preset.audio_bitrate,
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run(command)
    return output_path

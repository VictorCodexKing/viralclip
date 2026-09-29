"""MoviePy 2 clip extraction, face-centered cropping, and transition effects."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .common import AUDIO_BITRATE, FINAL_VIDEO_CRF, FINAL_VIDEO_PRESET, OUTPUT_FPS
from .reframing import detect_optimal_crop_region

TRANSITIONS_DIR = Path(__file__).resolve().parents[2] / "transitions"


def render_video_clip(
    video_path: Path,
    output_path: Path,
    start: float,
    end: float,
    *,
    vertical: bool = True,
    transition: str = "none",
) -> tuple[float, float]:
    """Render an H.264/AAC clip and return its actual source boundaries."""
    os.environ.setdefault("IMAGEIO_FFMPEG_EXE", shutil.which("ffmpeg") or "ffmpeg")
    from moviepy import VideoFileClip, afx, vfx

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with VideoFileClip(str(video_path)) as source:
        start = max(0.0, start)
        end = min(end, source.duration)
        if end <= start:
            raise ValueError("The selected range is outside the source video.")
        clip = source.subclipped(start, end)
        if vertical:
            x, y, width, height = detect_optimal_crop_region(video_path, start, end)
            clip = clip.cropped(x1=x, y1=y, width=width, height=height)
            # Preserve aspect ratio, then trim the small rounding surplus.
            clip = clip.resized(height=1920)
            if clip.w < 1080:
                clip = clip.resized(width=1080)
            clip = clip.cropped(width=1080, height=1920, x_center=clip.w / 2, y_center=clip.h / 2)
        elif clip.w % 2 or clip.h % 2:
            clip = clip.cropped(width=clip.w - clip.w % 2, height=clip.h - clip.h % 2)

        if transition == "fade":
            preset = json.loads((TRANSITIONS_DIR / "fade.json").read_text(encoding="utf-8"))
            duration = min(float(preset["duration_seconds"]), clip.duration / 2)
            clip = clip.with_effects([vfx.FadeIn(duration), vfx.FadeOut(duration)])
            if clip.audio is not None:
                clip = clip.with_audio(clip.audio.with_effects([afx.AudioFadeIn(duration), afx.AudioFadeOut(duration)]))
        elif transition != "none":
            raise ValueError(f"Unknown transition: {transition}")

        clip.write_videofile(
            str(output_path),
            fps=OUTPUT_FPS,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate=AUDIO_BITRATE,
            temp_audiofile=str(output_path.with_suffix(".audio.m4a")),
            remove_temp=True,
            preset=FINAL_VIDEO_PRESET,
            threads=2,
            ffmpeg_params=["-crf", str(FINAL_VIDEO_CRF), "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
            logger=None,
        )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError("MoviePy did not produce a video file.")
    return start, end

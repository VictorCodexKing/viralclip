"""Verify MoviePy writes playable vertical clips with audio and fade effects."""

import shutil
import subprocess

import pytest

from app.media import moviepy_render
from app.media.ffmpeg import ffprobe_duration, ffprobe_has_audio, ffprobe_video_size

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg is required")


def test_moviepy_vertical_clip_retains_audio_and_applies_fade(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    output = tmp_path / "vertical.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
        "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True)
    monkeypatch.setattr(moviepy_render, "detect_optimal_crop_region", lambda *args: (92, 0, 134, 240))
    start, end = moviepy_render.render_video_clip(source, output, 0.5, 1.5, transition="fade")
    assert (start, end) == (0.5, 1.5)
    assert ffprobe_video_size(output) == (1080, 1920)
    assert ffprobe_has_audio(output)
    assert ffprobe_duration(output) == pytest.approx(1.0, abs=0.1)
    from moviepy import VideoFileClip

    with VideoFileClip(str(output)) as video:
        assert video.get_frame(0).mean() < video.get_frame(0.5).mean() / 2

"""Exercise real subtitle rendering, including Windows filter path escaping."""

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app.media.ffmpeg import subtitles_filter_fragment

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")


def test_subtitles_render_from_paths_with_special_characters(tmp_path):
    directory = tmp_path / "Victor's clips [draft], ready"
    directory.mkdir()
    subtitles = directory / "words.ass"
    subtitles.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, BorderStyle, Outline, Shadow, Alignment, Encoding\n"
        "Style: D,Poppins ExtraBold,40,&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,READY\n",
        encoding="utf-8",
    )
    fonts = Path(__file__).resolve().parents[1] / "fonts"
    frame = tmp_path / "caption.png"
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
         "-vf", subtitles_filter_fragment(subtitles, fonts), "-frames:v", "1", str(frame)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    with Image.open(frame) as rendered:
        assert rendered.convert("L").getextrema()[1] > 100

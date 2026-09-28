"""Unit tests for ASS caption building and the hook-title overlay.

The emoji-support probe (which shells out to ffmpeg) is disabled, and word
timings are supplied directly so no transcript cache or network is needed.
"""

from __future__ import annotations

from app.media import captions
from app.media.captions import (
    build_hook_title_ass,
    hex_to_ass_color,
    build_ass_subtitles,
)

WORDS = [
    {"text": "This", "start": 0.0, "end": 0.4},
    {"text": "is", "start": 0.4, "end": 0.6},
    {"text": "a", "start": 0.6, "end": 0.7},
    {"text": "test", "start": 0.7, "end": 1.2},
    {"text": "clip", "start": 1.2, "end": 1.8},
]


def test_hex_to_ass_color_bgr_order():
    # #FF0000 (red) -> ASS BGR &H000000FF&
    assert hex_to_ass_color("#FF0000") == "&H000000FF&"
    # #00FF00 (green) -> &H0000FF00&
    assert hex_to_ass_color("#00FF00") == "&H0000FF00&"


def test_build_hook_title_ass_produces_style_and_events():
    style, events = build_hook_title_ass(
        "The 10x growth secret",
        captions.get_template("hormozi"),
        1080,
        1920,
        30.0,
        "Arial",
        48,
    )
    assert style.startswith("Style: Hook,")
    assert len(events) == 1
    assert events[0].startswith("Dialogue: 1,")


def test_build_ass_subtitles_writes_karaoke_events(monkeypatch, tmp_path):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    ass_path = tmp_path / "out.ass"
    ok = build_ass_subtitles(
        video_path=tmp_path / "video.mp4",  # no cache; we pass caption_words
        clip_start=0.0,
        clip_end=2.0,
        video_width=1080,
        video_height=1920,
        output_ass_path=ass_path,
        caption_template="default",
        caption_words=WORDS,
        hook_title="Watch this now",
    )
    assert ok is True
    content = ass_path.read_text(encoding="utf-8")
    assert "[V4+ Styles]" in content
    assert "Style: Hook," in content  # hook title style present
    assert "Dialogue:" in content
    # Karaoke default: one event per word per chunk -> more than one dialogue line.
    assert content.count("Dialogue:") >= len(WORDS)


def test_build_ass_subtitles_hook_only_when_no_words(monkeypatch, tmp_path):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    ass_path = tmp_path / "hook.ass"
    ok = build_ass_subtitles(
        video_path=tmp_path / "video.mp4",
        clip_start=0.0,
        clip_end=2.0,
        video_width=1080,
        video_height=1920,
        output_ass_path=ass_path,
        caption_words=[],
        include_captions=False,
        hook_title="Only a hook",
    )
    assert ok is True
    assert "Style: Hook," in ass_path.read_text(encoding="utf-8")

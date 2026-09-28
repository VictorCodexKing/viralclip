"""Unit tests for transcription formatting, captions, reframing, templates,
B-roll gating, and source-type detection -- all without network or real ffmpeg
renders (blocking calls are stubbed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.caption_templates import CAPTION_TEMPLATES, get_template
from app.media import reframing
from app.media.transcription import (
    _whisper_result_to_transcript_data,
    format_ms_to_timestamp,
    format_transcript_for_analysis,
)
from app.youtube_utils import (
    SOURCE_TYPE_LOCAL,
    SOURCE_TYPE_YOUTUBE,
    determine_source_type,
    get_youtube_video_id,
)


# --- Transcription formatting -------------------------------------------------

WHISPER_RESULT = {
    "text": "Hello world. This is a test.",
    "segments": [
        {
            "start": 0.0,
            "end": 2.5,
            "text": "Hello world.",
            "words": [
                {"word": "Hello", "start": 0.0, "end": 1.0, "probability": 0.9},
                {"word": "world.", "start": 1.0, "end": 2.5, "probability": 0.8},
            ],
        },
        {
            "start": 62.0,
            "end": 65.0,
            "text": "This is a test.",
            "words": [
                {"word": "This", "start": 62.0, "end": 63.0, "probability": 0.95},
            ],
        },
    ],
}


def test_format_ms_to_timestamp():
    assert format_ms_to_timestamp(0) == "00:00"
    assert format_ms_to_timestamp(65000) == "01:05"


def test_format_transcript_for_analysis_from_whisper_dict():
    lines = format_transcript_for_analysis(WHISPER_RESULT)
    assert lines[0] == "[00:00 - 00:02] Hello world."
    assert lines[1] == "[01:02 - 01:05] This is a test."


def test_whisper_result_to_cache_converts_seconds_to_ms():
    data = _whisper_result_to_transcript_data(WHISPER_RESULT)
    assert data["version"] >= 1
    assert data["text"].startswith("Hello world")
    # First word starts at 0ms, ends at 1000ms (1.0s * 1000).
    assert data["words"][0]["start"] == 0
    assert data["words"][0]["end"] == 1000
    assert data["words"][1]["end"] == 2500


# --- Caption templates --------------------------------------------------------


def test_caption_template_lookup_returns_merged_defaults():
    tpl = get_template("hormozi")
    assert tpl["name"] == "Hormozi"
    # Merged default key present even though it may be omitted per-template.
    assert "position_y" in tpl and "animation" in tpl


def test_unknown_template_falls_back_to_default():
    assert get_template("does-not-exist")["name"] == CAPTION_TEMPLATES["default"]["name"]


def test_seven_named_templates_exist():
    for name in ("default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"):
        assert name in CAPTION_TEMPLATES


# --- Reframing center-crop fallback ------------------------------------------


def test_reframe_center_crop_when_no_faces(monkeypatch):
    """With a mocked 1920x1080 source and no faces, the crop is centred 9:16."""
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda p: (1920, 1080))
    monkeypatch.setattr(reframing, "detect_faces_in_clip", lambda p, s, e: [])

    x, y, w, h = reframing.detect_optimal_crop_region(Path("dummy.mp4"), 0.0, 30.0)
    # 9:16 crop of a 1920x1080 frame -> width = 1080 * 9/16 = 607 -> even 606.
    assert h == 1080
    assert w == reframing.round_to_even(int(1080 * 9 / 16))
    # Centred horizontally: x = (1920 - w) // 2, y stays 0 (full height).
    assert x == reframing.round_to_even((1920 - w) // 2)
    assert y == 0


def test_reframe_uses_face_center_when_faces_present(monkeypatch):
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda p: (1920, 1080))
    # A single face far to the right; crop should shift right of centre.
    monkeypatch.setattr(
        reframing, "detect_faces_in_clip", lambda p, s, e: [(1600, 500, 40000, 0.9)]
    )
    x, _, w, _ = reframing.detect_optimal_crop_region(Path("dummy.mp4"), 0.0, 30.0)
    center_x = reframing.round_to_even((1920 - w) // 2)
    assert x > center_x  # biased toward the detected face


def test_compute_vertical_crop_dims_portrait_source():
    # A source that is already tall keeps full width and computes a 9:16 height.
    w, h = reframing.compute_vertical_crop_dims(1080, 2400)
    assert w == 1080
    assert h == reframing.round_to_even(int(1080 * 16 / 9))


# --- Source-type detection ----------------------------------------------------


def test_source_type_youtube():
    assert determine_source_type("https://youtu.be/dQw4w9WgXcQ") == SOURCE_TYPE_YOUTUBE
    assert (
        determine_source_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        == SOURCE_TYPE_YOUTUBE
    )


def test_source_type_local_file():
    assert determine_source_type("/tmp/uploads/video.mp4") == SOURCE_TYPE_LOCAL


def test_source_type_rejects_unknown():
    with pytest.raises(ValueError):
        determine_source_type("https://example.com/not-a-video")


def test_get_youtube_video_id_variants():
    assert get_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert get_youtube_video_id("not a url") is None


# --- B-roll graceful skip -----------------------------------------------------


async def test_broll_fetch_skipped_without_key(monkeypatch, tmp_path):
    from app import config as config_module
    from app.media import broll

    cfg = config_module.Config()
    cfg.pexels_api_key = None
    config_module.set_config_override(cfg)
    try:
        result = await broll.fetch_broll_for_opportunities(
            [{"search_term": "ocean", "timestamp": "00:05", "duration": 3.0}],
            tmp_path,
        )
        assert result == []
    finally:
        config_module.set_config_override(None)


def test_broll_overlay_noop_without_suggestions():
    from app.media import broll

    assert broll.overlay_broll(Path("x.mp4"), [], Path("out.mp4")) is False


# --- Export presets -----------------------------------------------------------


def test_export_presets_platforms_all_1080x1920():
    from app.clip_editor import EXPORT_PRESETS, get_export_preset

    for name in ("tiktok", "reels", "shorts"):
        preset = get_export_preset(name)
        assert (preset.width, preset.height) == (1080, 1920)
    assert set(EXPORT_PRESETS) == {"tiktok", "reels", "shorts"}


def test_export_preset_unknown_raises():
    from app.clip_editor import get_export_preset

    with pytest.raises(ValueError):
        get_export_preset("nope")

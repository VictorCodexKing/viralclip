"""Regressions for cached transcription, clip timing, and truthful completion."""

import json
from types import SimpleNamespace

import pytest

from app import pipeline
from app.ai import TranscriptAnalysis, TranscriptSegment
from app.config import Config
from app.media import captions, transcription


def test_assemblyai_word_cache_is_reused_without_upload(monkeypatch, tmp_path):
    cfg = Config()
    cfg.transcription_provider = "assemblyai"
    cfg.assembly_ai_api_key = "test-key"
    monkeypatch.setattr(transcription, "get_config", lambda: cfg)
    video = tmp_path / "video.mp4"
    transcript = SimpleNamespace(text="Useful cached words.", utterances=[], words=[
        SimpleNamespace(text="Useful", start=10000, end=11000, confidence=1),
        SimpleNamespace(text="cached", start=11000, end=12000, confidence=1),
        SimpleNamespace(text="words.", start=12000, end=14000, confidence=1),
    ])
    transcription.cache_transcript_data(video, transcript)
    def unexpected_upload(*args):
        pytest.fail("A cached transcript must not be uploaded again")
    monkeypatch.setattr(transcription, "_transcribe_with_assemblyai", unexpected_upload)
    assert transcription.get_video_transcript(video) == "[00:10 - 00:14] Useful cached words."


def test_render_captions_use_the_selected_source_range(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "words": [
            {"text": "WRONG", "start": 0, "end": 1000},
            {"text": "RIGHT", "start": 10000, "end": 11000},
        ],
    }), encoding="utf-8")
    temp = tmp_path / "temp"
    temp.mkdir()
    def fake_render(video, output, start, end, **kwargs):
        output.write_bytes(b"rendered")
        return start, end
    monkeypatch.setattr(pipeline, "render_video_clip", fake_render)
    monkeypatch.setattr(pipeline, "ffprobe_video_size", lambda _: (1080, 1920))
    monkeypatch.setattr(pipeline, "extend_keep_ranges_to_sentence_boundary", lambda _, ranges: ranges)
    monkeypatch.setattr(pipeline, "burn_ass_subtitles_ffmpeg", lambda *args: False)
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    result = pipeline._render_clip_sync(source, {"start_time": "00:10", "end_time": "00:20"}, 0, tmp_path / "output", temp, {})
    assert result is not None
    ass = next(temp.glob("*.ass")).read_text(encoding="utf-8")
    assert "RIGHT" in ass
    assert "WRONG" not in ass
    assert "0:00:00.00" in ass


@pytest.mark.parametrize("render_succeeds", [False, True])
async def test_job_only_completes_after_a_clip_is_saved(monkeypatch, tmp_path, store, render_succeeds):
    cfg = Config()
    cfg.transcription_provider = "assemblyai"
    cfg.assembly_ai_api_key = "test-key"
    cfg.output_dir = str(tmp_path / "outputs")
    cfg.temp_dir = str(tmp_path / "temp")
    monkeypatch.setattr(pipeline, "get_config", lambda: cfg)
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(pipeline, "get_video_transcript", lambda *args: "[00:00 - 00:20] Useful words for a clip.")
    async def analyse(*args, **kwargs):
        return TranscriptAnalysis(most_relevant_segments=[TranscriptSegment(start_time="00:00", end_time="00:20", text="Useful words for a clip.")], summary="Test", key_topics=[])
    monkeypatch.setattr(pipeline, "get_most_relevant_parts_by_transcript", analyse)
    output = tmp_path / "clip.mp4"
    output.write_bytes(b"clip")
    monkeypatch.setattr(pipeline, "_render_clip_sync", lambda *args: {"filename": output.name, "file_path": str(output), "start_time": 0, "end_time": 20, "duration": 20} if render_succeeds else None)
    job = store.create_job(source_type="local")
    if render_succeeds:
        await pipeline.process_video(job.id, str(source), "local")
        final = store.get_job(job.id)
        assert final.status == "completed"
        assert len(final.clips) == 1
        assert final.progress == 100
    else:
        with pytest.raises(RuntimeError, match="No clips could be rendered"):
            await pipeline.process_video(job.id, str(source), "local")
        final = store.get_job(job.id)
        assert final.status == "error"
        assert final.clips == []

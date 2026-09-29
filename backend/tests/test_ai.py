"""Unit tests for the AI analysis layer.

These exercise the real validation/repair logic (not static constants): at least
one test here fails if the segment-bound repair or the hook-title sanitizer are
reverted. The network-bound pydantic-ai Agent is mocked so selection is testable
without a real API call.
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from app import ai
from app.ai import (
    IDEAL_CLIP_MAX_SECONDS,
    MAX_ACCEPTED_CLIP_SECONDS,
    MIN_ACCEPTED_CLIP_SECONDS,
    TranscriptAnalysis,
    TranscriptSegment,
    ViralityAnalysis,
    _choose_repaired_bounds,
    _parse_transcript_spans,
    _parse_transcript_timestamp_seconds,
    build_transcript_analysis_prompt,
    get_most_relevant_parts_by_transcript,
    sanitize_hook_title,
)

# A synthetic timestamped transcript covering 0..80s in 5s spans.
TRANSCRIPT = "\n".join(
    f"[{i // 60:02d}:{i % 60:02d} - {(i + 5) // 60:02d}:{(i + 5) % 60:02d}] "
    f"Line {idx} has enough words to pass validation here."
    for idx, i in enumerate(range(0, 80, 5))
)


def _spans():
    return _parse_transcript_spans(TRANSCRIPT)


# --- Timestamp round-trip -----------------------------------------------------


@pytest.mark.parametrize("mmss,seconds", [("00:00", 0), ("01:30", 90), ("10:05", 605)])
def test_timestamp_parse_and_format_round_trip(mmss, seconds):
    assert ai._parse_transcript_timestamp_seconds(mmss) == seconds
    assert ai._format_transcript_timestamp(seconds) == mmss


def test_timestamp_parse_hh_mm_ss():
    assert _parse_transcript_timestamp_seconds("01:02:03") == 3723


# --- Hook title sanitizer -----------------------------------------------------


def test_sanitize_hook_title_strips_wrapping_quotes_and_hashtags():
    # Fully-wrapped title: outer quotes stripped, hashtag removed.
    assert sanitize_hook_title('  "Big secret revealed" ') == "Big secret revealed"
    assert sanitize_hook_title("Save money now #viral #tips") == "Save money now"


def test_sanitize_hook_title_drops_trailing_period_keeps_question():
    assert sanitize_hook_title("Why nobody tells you this.") == "Why nobody tells you this"
    assert sanitize_hook_title("Is this the future?") == "Is this the future?"


def test_sanitize_hook_title_word_cap():
    raw = " ".join(f"word{i}" for i in range(20))
    result = sanitize_hook_title(raw)
    assert result is not None
    assert len(result.split()) <= ai.HOOK_TITLE_MAX_WORDS


def test_sanitize_hook_title_empty_returns_none():
    assert sanitize_hook_title("") is None
    assert sanitize_hook_title("   ") is None
    assert sanitize_hook_title(None) is None


# --- Segment-bound repair -----------------------------------------------------


def test_repair_expands_too_short_segment():
    # 5s span is under the 10s minimum -> should expand.
    repaired = _choose_repaired_bounds(_spans(), 0, 5)
    assert repaired is not None
    start, end = repaired
    assert end - start >= MIN_ACCEPTED_CLIP_SECONDS
    assert end - start <= MAX_ACCEPTED_CLIP_SECONDS


def test_repair_trims_too_long_segment():
    # 75s span exceeds the 30s max -> should trim toward the ideal max.
    repaired = _choose_repaired_bounds(_spans(), 0, 75)
    assert repaired is not None
    start, end = repaired
    assert start == 0
    assert MIN_ACCEPTED_CLIP_SECONDS <= end - start <= IDEAL_CLIP_MAX_SECONDS


def test_repair_leaves_in_range_segment_untouched():
    # 30s span (00:00-00:30) is already valid -> no repair.
    assert _choose_repaired_bounds(_spans(), 0, 30) is None


# --- Virality schema ----------------------------------------------------------


def test_virality_defaults_and_clamping():
    v = ViralityAnalysis()
    assert v.hook_score == 15 and v.total_score == 60
    with pytest.raises(Exception):
        ViralityAnalysis(hook_score=99)  # ge/le bounds enforced


def test_transcript_segment_relevance_percent_coercion():
    seg = TranscriptSegment(
        start_time="00:00", end_time="00:30", text="hello world here", relevance_score=90
    )
    assert seg.relevance_score == pytest.approx(0.9)
    seg2 = TranscriptSegment(
        start_time="00:00", end_time="00:30", text="hello world here", relevance_score=0.4
    )
    assert seg2.relevance_score == pytest.approx(0.4)


def test_transcript_segment_text_alias():
    seg = TranscriptSegment(start_time="00:00", end_time="00:30", segment="aliased text here")
    assert seg.text == "aliased text here"


# --- Prompt builder -----------------------------------------------------------


def test_build_prompt_includes_broll_key_only_when_requested():
    with_broll = build_transcript_analysis_prompt("x", include_broll=True)
    without = build_transcript_analysis_prompt("x", include_broll=False)
    assert "broll_opportunities" in with_broll
    assert "broll_opportunities" not in without


# --- Mocked Agent selection ---------------------------------------------------


class _FakeResult:
    def __init__(self, output):
        self.output = output


class _FakeAgent:
    def __init__(self, output):
        self._output = output

    async def run(self, _prompt):
        return _FakeResult(self._output)


async def test_real_agent_constructor_and_structured_output(monkeypatch):
    """Exercise the installed Pydantic AI, rather than mocking its constructor."""
    from pydantic_ai.models.test import TestModel
    from app.config import Config

    cfg = Config()
    cfg.google_api_key = "test-key"
    cfg.llm = "google-gla:gemini-2.5-flash"
    model = TestModel(custom_output_args={
        "most_relevant_segments": [{"start_time": "00:00", "end_time": "00:20", "text": "Line 0 has enough words to pass validation here."}],
        "summary": "A test video.",
        "key_topics": ["testing"],
    })
    monkeypatch.setattr(ai, "get_config", lambda: cfg)
    monkeypatch.setattr(ai, "_build_transcript_model", lambda _: model)
    monkeypatch.setattr(ai, "_transcript_agent", None)
    monkeypatch.setattr(ai, "_transcript_agent_signature", None)
    result = await get_most_relevant_parts_by_transcript(TRANSCRIPT)
    assert len(result.most_relevant_segments) == 1
    assert result.most_relevant_segments[0].end_time == "00:20"


@pytest.mark.parametrize("end", ["00:10", "00:30"])
def test_requested_clip_duration_boundaries_are_accepted(end):
    segment = TranscriptSegment(start_time="00:00", end_time=end, text="A useful complete moment.")
    result = ai._validate_segments([segment], TRANSCRIPT)
    assert len(result) == 1
    assert result[0].end_time == end


async def test_ai_selection_repairs_and_validates_via_mocked_agent(monkeypatch):
    """A too-short segment from the model gets repaired; the hook title sanitized.

    This asserts the pipeline through validation actually runs the repair +
    sanitizer, so it fails if either is reverted.
    """
    raw = TranscriptAnalysis(
        most_relevant_segments=[
            TranscriptSegment(
                start_time="00:00",
                end_time="00:05",  # too short -> must be repaired/expanded
                text="Line 0 has enough words to pass validation here.",
                relevance_score=0.9,
                virality=ViralityAnalysis(
                    hook_score=20,
                    engagement_score=20,
                    value_score=20,
                    shareability_score=20,
                    total_score=0,  # wrong; validator should recompute to 80
                ),
                hook_title="  Amazing hook secret #clip ",
            )
        ],
        summary="A test video.",
        key_topics=["testing"],
    )
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: _FakeAgent(raw))

    result = await get_most_relevant_parts_by_transcript(TRANSCRIPT)
    assert len(result.most_relevant_segments) == 1
    seg = result.most_relevant_segments[0]

    duration = _parse_transcript_timestamp_seconds(
        seg.end_time
    ) - _parse_transcript_timestamp_seconds(seg.start_time)
    assert duration >= MIN_ACCEPTED_CLIP_SECONDS  # repaired from 5s
    assert seg.virality.total_score == 80  # recomputed from subscores
    assert seg.hook_title == "Amazing hook secret"  # sanitized (hashtag + ws)


async def test_ai_selection_drops_invalid_duration_segment(monkeypatch):
    raw = TranscriptAnalysis(
        most_relevant_segments=[
            TranscriptSegment(
                start_time="00:10",
                end_time="00:10",  # identical -> dropped
                text="Line has enough words here to count.",
            )
        ],
        summary="s",
        key_topics=["t"],
    )
    monkeypatch.setattr(ai, "get_transcript_agent", lambda: _FakeAgent(raw))
    result = await get_most_relevant_parts_by_transcript(TRANSCRIPT)
    assert result.most_relevant_segments == []


@pytest.mark.parametrize("status,failures,expected_calls,expected_delays", [
    (503, 2, 3, [2, 4]),
    (503, 4, 4, [2, 4, 8]),
    (404, 1, 1, []),
])
async def test_analysis_retries_only_transient_failures(
    monkeypatch, status, failures, expected_calls, expected_delays,
):
    raw = TranscriptAnalysis(most_relevant_segments=[], summary="s", key_topics=[])
    calls = 0
    delays = []

    class IntermittentAgent(_FakeAgent):
        async def run(self, prompt):
            nonlocal calls
            calls += 1
            if calls <= failures:
                raise ModelHTTPError(status_code=status, model_name="test", body={})
            return await super().run(prompt)

    async def no_wait(delay):
        delays.append(delay)

    monkeypatch.setattr(ai, "get_transcript_agent", lambda: IntermittentAgent(raw))
    monkeypatch.setattr(ai.asyncio, "sleep", no_wait)
    if status == 404 or failures >= 4:
        with pytest.raises(RuntimeError, match="Transcript analysis failed"):
            await get_most_relevant_parts_by_transcript(TRANSCRIPT)
    else:
        assert (await get_most_relevant_parts_by_transcript(TRANSCRIPT)).summary == "s"
    assert calls == expected_calls
    assert delays == expected_delays

"""Async video-processing pipeline orchestration.

Adapted from the reference ``video_service.process_video_complete`` staging.
Turns a YouTube URL (or a local uploaded video) into vertical 9:16 clips:

    10%  download / locate source
    30%  transcribe (local Whisper by default)
    50%  AI clip selection + virality scoring + hook titles
    70%  render each selected segment:
           reframe to 9:16 (face-centered) -> burn word-synced subtitles +
           hook title -> optional Pexels B-roll -> write mp4 to data/outputs
   100%  complete

Progress is pushed through ``jobstore.update_job_progress`` (which fans out to
the SSE pub/sub), and each rendered clip is persisted via ``jobstore.add_clip``
with its full virality fields, hook title, and clip order. All blocking work
(ffmpeg / Whisper / OpenCV) runs in a thread executor so the event loop stays
responsive; on error the job is marked failed via ``jobstore.set_job_error``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import jobstore
from .ai import get_most_relevant_parts_by_transcript
from .config import get_config
from .media.broll import fetch_broll_for_opportunities, overlay_broll
from .media.captions import build_ass_subtitles
from .media.ffmpeg import (
    burn_ass_subtitles_ffmpeg,
    extract_clip,
    ffprobe_video_size,
)
from .media.reframing import reframe_to_vertical
from .media.timeline import (
    extend_keep_ranges_to_sentence_boundary,
    parse_timestamp_to_seconds,
)
from .media.transcription import get_video_transcript
from .youtube_utils import (
    SOURCE_TYPE_LOCAL,
    SOURCE_TYPE_YOUTUBE,
    determine_source_type,
    download_youtube_video,
)

logger = logging.getLogger("viralclip.pipeline")


async def _run(func, *args):
    """Run a blocking callable in the default thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


def _segment_to_payload(segment: Any) -> Dict[str, Any]:
    """Flatten a TranscriptSegment (or dict) into a persistable payload."""
    if isinstance(segment, dict):
        virality = segment.get("virality") or {}
        if hasattr(virality, "model_dump"):
            virality = virality.model_dump()
        return {
            "start_time": segment.get("start_time"),
            "end_time": segment.get("end_time"),
            "text": segment.get("text", ""),
            "relevance_score": segment.get("relevance_score", 0.0),
            "reasoning": segment.get("reasoning", ""),
            "virality_score": virality.get("total_score", 0),
            "hook_score": virality.get("hook_score", 0),
            "engagement_score": virality.get("engagement_score", 0),
            "value_score": virality.get("value_score", 0),
            "shareability_score": virality.get("shareability_score", 0),
            "hook_type": virality.get("hook_type"),
            "hook_title": segment.get("hook_title"),
        }
    virality = segment.virality.model_dump() if segment.virality else {}
    return {
        "start_time": segment.start_time,
        "end_time": segment.end_time,
        "text": segment.text,
        "relevance_score": segment.relevance_score,
        "reasoning": segment.reasoning,
        "virality_score": virality.get("total_score", 0),
        "hook_score": virality.get("hook_score", 0),
        "engagement_score": virality.get("engagement_score", 0),
        "value_score": virality.get("value_score", 0),
        "shareability_score": virality.get("shareability_score", 0),
        "hook_type": virality.get("hook_type"),
        "hook_title": getattr(segment, "hook_title", None),
    }


def _render_clip_sync(
    video_path: Path,
    segment: Dict[str, Any],
    clip_index: int,
    output_dir: Path,
    temp_dir: Path,
    options: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Render one segment into a finished vertical mp4 (blocking work)."""
    start_seconds = parse_timestamp_to_seconds(segment["start_time"])
    end_seconds = parse_timestamp_to_seconds(segment["end_time"])
    if end_seconds <= start_seconds:
        logger.warning("Skipping clip %d: invalid duration", clip_index + 1)
        return None

    keep_ranges = extend_keep_ranges_to_sentence_boundary(
        video_path, [(start_seconds, end_seconds)]
    )
    if keep_ranges:
        start_seconds = keep_ranges[0][0]
        end_seconds = keep_ranges[-1][1]

    suffix = uuid.uuid4().hex[:8]
    trimmed_path = temp_dir / f"trim_{clip_index + 1}_{suffix}.mp4"
    vertical_path = temp_dir / f"vertical_{clip_index + 1}_{suffix}.mp4"
    filename = (
        f"clip_{clip_index + 1}_"
        f"{segment['start_time'].replace(':', '')}-"
        f"{segment['end_time'].replace(':', '')}_{suffix}.mp4"
    )
    output_path = output_dir / filename

    # 1) Trim the source range.
    if not extract_clip(video_path, start_seconds, end_seconds, trimmed_path):
        logger.error("Failed to trim clip %d", clip_index + 1)
        return None

    # 2) Reframe to face-centered 9:16 (falls back to center crop).
    if options.get("output_format", "vertical") == "vertical":
        if not reframe_to_vertical(trimmed_path, vertical_path, 0.0, end_seconds - start_seconds):
            logger.warning("Reframe failed for clip %d; using trimmed source", clip_index + 1)
            vertical_path = trimmed_path
    else:
        vertical_path = trimmed_path

    try:
        video_width, video_height = ffprobe_video_size(vertical_path)
    except Exception:
        video_width, video_height = 1080, 1920

    # 3) Burn word-synced subtitles + hook title.
    final_source = vertical_path
    if options.get("add_subtitles", True):
        ass_path = temp_dir / f"subs_{clip_index + 1}_{suffix}.ass"
        built = build_ass_subtitles(
            video_path=video_path,
            clip_start=start_seconds,
            clip_end=end_seconds,
            video_width=video_width,
            video_height=video_height,
            output_ass_path=ass_path,
            font_family=options.get("font_family"),
            font_size=options.get("font_size"),
            font_color=options.get("font_color"),
            caption_template=options.get("caption_template", "default"),
            keep_ranges=[(0.0, end_seconds - start_seconds)],
            hook_title=segment.get("hook_title"),
            include_captions=True,
        )
        if built:
            captioned_path = temp_dir / f"cap_{clip_index + 1}_{suffix}.mp4"
            fonts_dir = ass_path.parent  # subtitles filter reads fontsdir
            from .font_registry import FONTS_DIR

            if burn_ass_subtitles_ffmpeg(
                vertical_path, ass_path, captioned_path, FONTS_DIR
            ):
                final_source = captioned_path

    # 4) Move/copy the finished clip to the outputs dir.
    output_dir.mkdir(parents=True, exist_ok=True)
    if final_source != output_path:
        import shutil

        shutil.copyfile(final_source, output_path)

    return {
        "filename": filename,
        "file_path": str(output_path),
        "start_time": start_seconds,
        "end_time": end_seconds,
        "duration": end_seconds - start_seconds,
    }


async def process_video(
    job_id: str,
    url_or_path: str,
    source_type: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    progress_cb=None,
) -> None:
    """Run the full pipeline for ``job_id``, updating progress as it goes."""
    options = options or {}
    config = get_config()
    output_dir = Path(config.output_dir)
    temp_dir = Path(config.temp_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    async def emit(progress: int, message: str, status: str = jobstore.STATUS_PROCESSING):
        jobstore.update_job_progress(job_id, progress=progress, message=message, status=status)
        if progress_cb is not None:
            await progress_cb(progress, message, status)

    try:
        if source_type is None:
            source_type = determine_source_type(url_or_path)

        # --- 10%: obtain the source video --------------------------------
        await emit(10, "Downloading video...")
        if source_type == SOURCE_TYPE_YOUTUBE:
            video_path = await _run(
                download_youtube_video, url_or_path, 3, config.max_video_duration
            )
            if not video_path:
                raise RuntimeError("Failed to download video")
        elif source_type == SOURCE_TYPE_LOCAL:
            video_path = Path(url_or_path)
            if not video_path.exists():
                raise RuntimeError("Video file not found")
        else:
            raise ValueError(f"Unsupported source type: {source_type}")

        # --- 30%: transcribe --------------------------------------------
        await emit(30, "Generating transcript...")
        source_url = url_or_path if source_type == SOURCE_TYPE_YOUTUBE else None
        transcript = await _run(get_video_transcript, video_path, "universal", source_url)
        if not transcript.strip():
            raise RuntimeError("Transcription produced no text")

        # --- 50%: AI clip selection -------------------------------------
        await emit(50, "Analyzing content with AI...")
        include_broll = bool(options.get("include_broll")) and bool(config.pexels_api_key)
        analysis = await get_most_relevant_parts_by_transcript(
            transcript, include_broll=include_broll
        )
        segments = [_segment_to_payload(s) for s in analysis.most_relevant_segments]
        segments = segments[: config.max_clips]
        if not segments:
            raise RuntimeError("AI analysis selected no clip-worthy segments")

        # --- 70%: render each clip --------------------------------------
        await emit(70, "Creating video clips...")
        broll_suggestions: List[Dict[str, Any]] = []
        if include_broll and analysis.broll_opportunities:
            opportunities = [o.model_dump() for o in analysis.broll_opportunities]
            broll_suggestions = await fetch_broll_for_opportunities(
                opportunities, temp_dir
            )

        total = len(segments)
        for index, segment in enumerate(segments):
            progress = 70 + int(25 * (index / max(1, total)))
            await emit(progress, f"Rendering clip {index + 1}/{total}...")
            rendered = await _run(
                _render_clip_sync,
                video_path,
                segment,
                index,
                output_dir,
                temp_dir,
                options,
            )
            if not rendered:
                continue

            # Optional B-roll overlay (best-effort, never fails the pipeline).
            if broll_suggestions:
                overlaid = temp_dir / f"broll_{index + 1}_{uuid.uuid4().hex[:8]}.mp4"
                if await _run(
                    overlay_broll,
                    Path(rendered["file_path"]),
                    broll_suggestions,
                    overlaid,
                ):
                    import shutil

                    shutil.copyfile(overlaid, rendered["file_path"])

            jobstore.add_clip(
                job_id=job_id,
                filename=rendered["filename"],
                file_path=rendered["file_path"],
                start_time=rendered["start_time"],
                end_time=rendered["end_time"],
                duration=rendered["duration"],
                text=segment.get("text"),
                relevance_score=segment.get("relevance_score"),
                reasoning=segment.get("reasoning"),
                virality_score=segment.get("virality_score"),
                hook_score=segment.get("hook_score"),
                engagement_score=segment.get("engagement_score"),
                value_score=segment.get("value_score"),
                shareability_score=segment.get("shareability_score"),
                hook_type=segment.get("hook_type"),
                hook_title=segment.get("hook_title"),
                clip_order=index,
            )

        # --- 100%: done --------------------------------------------------
        await emit(100, "Processing complete", jobstore.STATUS_COMPLETED)
    except Exception as exc:
        logger.error("Pipeline failed for job %s: %s", job_id, exc)
        jobstore.set_job_error(job_id, str(exc))
        if progress_cb is not None:
            try:
                await progress_cb(0, str(exc), jobstore.STATUS_ERROR)
            except Exception:  # pragma: no cover
                pass
        raise

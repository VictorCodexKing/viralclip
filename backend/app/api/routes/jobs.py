"""Auth-free REST + SSE API for the ViralClip pipeline and built-in editor.

Adapted (route shapes) from the reference ``api/routes/tasks.py`` but stripped
of all authentication, billing, share-token, and ownership logic. Everything
runs in a single process on one laptop:

* jobs are created by pasting a YouTube URL or uploading a video file,
* the pipeline runs as an in-process ``asyncio`` background task,
* live progress is streamed over SSE sourced from the jobstore's in-process
  pub/sub (this replaces the reference project's Redis pub/sub), and
* clips can be served, downloaded, trimmed, split, merged, and exported with
  platform presets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from ... import caption_templates, font_registry, jobstore, pipeline
from ...clip_editor import (
    EXPORT_PRESETS,
    ffprobe_duration,
    export_with_preset,
    merge_clip_files,
    split_clip_file,
    trim_clip_file,
)
from ...config import get_config
from ...youtube_utils import determine_source_type

logger = logging.getLogger("viralclip.api")

router = APIRouter(prefix="/api", tags=["jobs"])

UPLOADS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "uploads"
# 5 GB upload sanity cap; the real max-duration guard lives in the pipeline.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024
_VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


# --- Serialization helpers ----------------------------------------------------


def _clip_to_dict(clip: jobstore.Clip) -> dict[str, Any]:
    return asdict(clip)


def _job_to_dict(job: jobstore.Job) -> dict[str, Any]:
    data = asdict(job)
    data["clips"] = [_clip_to_dict(c) for c in job.clips]
    return data


# --- Input validation ---------------------------------------------------------


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _normalize_font_size(value: Any) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(12, min(72, parsed))


def _normalize_font_color(value: Any) -> str | None:
    if isinstance(value, str) and re.match(r"^#[0-9A-Fa-f]{6}$", value.strip()):
        return value.strip().upper()
    return None


def _normalize_font_family(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalize_options(raw: Any) -> dict[str, Any]:
    """Whitelist and normalize the caption/render options from the request."""
    options: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return options

    font_family = _normalize_font_family(raw.get("font_family"))
    if font_family is not None:
        options["font_family"] = font_family
    font_size = _normalize_font_size(raw.get("font_size"))
    if font_size is not None:
        options["font_size"] = font_size
    font_color = _normalize_font_color(raw.get("font_color"))
    if font_color is not None:
        options["font_color"] = font_color

    caption_template = raw.get("caption_template")
    if isinstance(caption_template, str) and caption_template.strip():
        options["caption_template"] = caption_template.strip()

    output_format = raw.get("output_format")
    if output_format in ("vertical", "original"):
        options["output_format"] = output_format

    options["include_broll"] = bool(raw.get("include_broll", False))
    options["add_subtitles"] = bool(raw.get("add_subtitles", True))

    provider = raw.get("transcription_provider")
    if isinstance(provider, str) and provider.strip():
        options["transcription_provider"] = provider.strip()

    return options


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return payload


def _get_job_or_404(job_id: str) -> jobstore.Job:
    job = jobstore.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _get_clip_or_404(job_id: str, clip_id: str) -> jobstore.Clip:
    clip = jobstore.get_clip(clip_id)
    if clip is None or clip.job_id != job_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    return clip


async def _run_pipeline_background(
    job_id: str, source: str, source_type: str, options: dict[str, Any]
) -> None:
    """Run the async pipeline as an in-process background task.

    Registered via ``BackgroundTasks`` so it starts after the response is sent.
    Errors are already recorded on the job by the pipeline itself; we swallow
    the re-raise here so a failed render never crashes the server task.
    """
    try:
        await pipeline.process_video(
            job_id,
            source,
            source_type=source_type,
            options=options,
        )
    except Exception:  # pragma: no cover - pipeline records the error itself
        logger.exception("Pipeline task failed for job %s", job_id)


# --- Job creation & upload ----------------------------------------------------


@router.post("/jobs")
async def create_job(request: Request, background_tasks: BackgroundTasks):
    """Create a job from a YouTube URL or an uploaded video and start it."""
    data = await _read_json_object(request)

    raw_source = data.get("source")
    if (
        not isinstance(raw_source, dict)
        or not isinstance(raw_source.get("url"), str)
        or not raw_source["url"].strip()
    ):
        raise HTTPException(status_code=400, detail="source.url is required")

    source = raw_source["url"].strip()
    try:
        source_type = determine_source_type(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    options = _normalize_options(data.get("options"))

    stored_url = source if source_type != "local" else None
    job = jobstore.create_job(
        source_url=stored_url,
        source_type=source_type,
        options=options,
    )

    background_tasks.add_task(
        _run_pipeline_background, job.id, source, source_type, options
    )

    return {"job_id": job.id, "status": job.status}


@router.post("/uploads")
async def upload_video(file: UploadFile = File(...)):
    """Accept a long video upload and return a reference usable as a job source."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Upload one of: "
                + ", ".join(sorted(_VIDEO_SUFFIXES))
            ),
        )

    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOADS_DIR / stored_name

    size = 0
    try:
        async with aiofiles.open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    await out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="File too large")
                await out.write(chunk)
    finally:
        await file.close()

    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    return {
        "source": {"url": str(dest)},
        "source_type": "local",
        "filename": file.filename,
        "path": str(dest),
        "size": size,
    }


# --- Listing & retrieval ------------------------------------------------------


@router.get("/jobs")
async def list_jobs(limit: int = Query(default=50, ge=1, le=200)):
    jobs = jobstore.list_jobs(limit=limit)
    return {"jobs": [_job_to_dict(job) for job in jobs], "total": len(jobs)}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = _get_job_or_404(job_id)
    return _job_to_dict(job)


# --- SSE progress -------------------------------------------------------------


@router.get("/jobs/{job_id}/progress")
async def job_progress_sse(job_id: str, request: Request):
    """Stream live pipeline progress over Server-Sent Events."""
    job = _get_job_or_404(job_id)

    async def event_generator():
        # Emit the current status immediately so late subscribers catch up.
        yield {
            "event": "status",
            "data": json.dumps(
                {
                    "job_id": job.id,
                    "status": job.status,
                    "progress": job.progress,
                    "message": job.progress_message or "",
                }
            ),
        }

        if job.status in (jobstore.STATUS_COMPLETED, jobstore.STATUS_ERROR):
            yield {"event": "close", "data": json.dumps({"status": job.status})}
            return

        queue = await jobstore.subscribe(job_id)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies from closing the idle connection.
                    yield {"event": "ping", "data": "{}"}
                    continue

                status = event.get("status")
                yield {
                    "event": "progress",
                    "data": json.dumps(
                        {
                            "job_id": job_id,
                            "status": status,
                            "progress": event.get("progress"),
                            "message": event.get("message")
                            or event.get("error")
                            or "",
                        }
                    ),
                }

                if status in (jobstore.STATUS_COMPLETED, jobstore.STATUS_ERROR):
                    yield {
                        "event": "close",
                        "data": json.dumps({"status": status}),
                    }
                    break
        finally:
            await jobstore.unsubscribe(job_id, queue)

    return EventSourceResponse(event_generator())


# --- Clip file serving --------------------------------------------------------


@router.get("/jobs/{job_id}/clips/{clip_id}/file")
async def get_clip_file(job_id: str, clip_id: str, download: int = 0):
    """Serve a clip mp4 inline (range-enabled) or as a download attachment."""
    clip = _get_clip_or_404(job_id, clip_id)
    if not clip.file_path:
        raise HTTPException(status_code=404, detail="Clip file not found")
    clip_path = Path(clip.file_path)
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    disposition = "attachment" if download else "inline"
    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=clip.filename or clip_path.name,
        content_disposition_type=disposition,
        headers={"Cache-Control": "private, no-store"},
    )


# --- Editor: trim / split / merge / export ------------------------------------


def _outputs_dir() -> Path:
    return Path(get_config().output_dir)


@router.patch("/jobs/{job_id}/clips/{clip_id}")
async def trim_clip(job_id: str, clip_id: str, request: Request):
    """Trim a clip's front/end offsets and regenerate the file in place."""
    payload = await _read_json_object(request)
    try:
        start_offset = _finite_number(payload.get("start_offset", 0), "start_offset")
        end_offset = _finite_number(payload.get("end_offset", 0), "end_offset")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if start_offset < 0 or end_offset < 0:
        raise HTTPException(status_code=400, detail="Offsets must be non-negative")

    clip = _get_clip_or_404(job_id, clip_id)
    if not clip.file_path or not Path(clip.file_path).exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    input_path = Path(clip.file_path)
    loop = asyncio.get_event_loop()
    new_path = await loop.run_in_executor(
        None,
        trim_clip_file,
        input_path,
        _outputs_dir(),
        start_offset,
        end_offset,
    )

    # Recompute the duration of the regenerated file and shift the clip's
    # start/end bounds so the editor sliders and split guard use the new length.
    new_duration = await loop.run_in_executor(None, ffprobe_duration, new_path)
    fields: dict[str, Any] = {
        "file_path": str(new_path),
        "filename": new_path.name,
        "duration": new_duration,
    }
    if clip.start_time is not None:
        fields["start_time"] = clip.start_time + start_offset
        fields["end_time"] = clip.start_time + start_offset + new_duration
    else:
        fields["start_time"] = 0.0
        fields["end_time"] = new_duration

    updated = jobstore.update_clip(clip_id, **fields)
    return {"clip": _clip_to_dict(updated)}


@router.post("/jobs/{job_id}/clips/{clip_id}/split")
async def split_clip(job_id: str, clip_id: str, request: Request):
    """Split a clip into two clips at ``split_time`` seconds."""
    payload = await _read_json_object(request)
    try:
        split_time = _finite_number(payload.get("split_time", 0), "split_time")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if split_time <= 0:
        raise HTTPException(
            status_code=400, detail="split_time must be greater than zero"
        )

    clip = _get_clip_or_404(job_id, clip_id)
    if not clip.file_path or not Path(clip.file_path).exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    loop = asyncio.get_event_loop()
    first_path, second_path = await loop.run_in_executor(
        None,
        split_clip_file,
        Path(clip.file_path),
        _outputs_dir(),
        split_time,
    )

    first_duration = await loop.run_in_executor(None, ffprobe_duration, first_path)
    second_duration = await loop.run_in_executor(None, ffprobe_duration, second_path)

    base_start = clip.start_time or 0.0

    first = jobstore.update_clip(
        clip_id,
        file_path=str(first_path),
        filename=first_path.name,
        duration=first_duration,
        start_time=base_start,
        end_time=base_start + first_duration,
    )
    # The two halves inherit the parent clip's virality scores and metadata so
    # their cards still render a score bar rather than an empty placeholder.
    second = jobstore.add_clip(
        job_id=job_id,
        filename=second_path.name,
        file_path=str(second_path),
        start_time=base_start + first_duration,
        end_time=base_start + first_duration + second_duration,
        duration=second_duration,
        text=clip.text,
        relevance_score=clip.relevance_score,
        reasoning=clip.reasoning,
        virality_score=clip.virality_score,
        hook_score=clip.hook_score,
        engagement_score=clip.engagement_score,
        value_score=clip.value_score,
        shareability_score=clip.shareability_score,
        hook_type=clip.hook_type,
        hook_title=clip.hook_title,
        clip_order=clip.clip_order + 1,
    )
    return {"clips": [_clip_to_dict(first), _clip_to_dict(second)]}


@router.post("/jobs/{job_id}/clips/merge")
async def merge_clips(job_id: str, request: Request):
    """Merge multiple clips (in order) into a single new clip."""
    payload = await _read_json_object(request)
    clip_ids = payload.get("clip_ids") or []
    if (
        not isinstance(clip_ids, list)
        or len(clip_ids) < 2
        or any(not isinstance(cid, str) or not cid for cid in clip_ids)
    ):
        raise HTTPException(
            status_code=400,
            detail="clip_ids must be an array of at least two clip ids",
        )

    paths: list[Path] = []
    source_clips: list[jobstore.Clip] = []
    for cid in clip_ids:
        clip = _get_clip_or_404(job_id, cid)
        if not clip.file_path or not Path(clip.file_path).exists():
            raise HTTPException(
                status_code=404, detail=f"Clip file not found: {cid}"
            )
        paths.append(Path(clip.file_path))
        source_clips.append(clip)

    loop = asyncio.get_event_loop()
    merged_path = await loop.run_in_executor(
        None, merge_clip_files, paths, _outputs_dir()
    )

    merged_duration = await loop.run_in_executor(None, ffprobe_duration, merged_path)

    # The merged clip inherits scores/metadata from the strongest source clip
    # so its card shows a score bar rather than an empty placeholder.
    best = max(
        source_clips,
        key=lambda c: c.virality_score if c.virality_score is not None else -1.0,
    )
    merged = jobstore.add_clip(
        job_id=job_id,
        filename=merged_path.name,
        file_path=str(merged_path),
        start_time=0.0,
        end_time=merged_duration,
        duration=merged_duration,
        text=best.text,
        relevance_score=best.relevance_score,
        reasoning=best.reasoning,
        virality_score=best.virality_score,
        hook_score=best.hook_score,
        engagement_score=best.engagement_score,
        value_score=best.value_score,
        shareability_score=best.shareability_score,
        hook_type=best.hook_type,
        hook_title=best.hook_title,
        clip_order=9999,
    )
    return {"clip": _clip_to_dict(merged)}


@router.post("/jobs/{job_id}/clips/{clip_id}/export")
async def export_clip(job_id: str, clip_id: str, request: Request):
    """Re-encode a clip for a platform preset (tiktok / reels / shorts)."""
    payload = await _read_json_object(request)
    preset_raw = payload.get("preset", "tiktok")
    preset_name = preset_raw.lower().strip() if isinstance(preset_raw, str) else ""
    if preset_name not in EXPORT_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid preset. Use one of: {', '.join(EXPORT_PRESETS.keys())}",
        )

    clip = _get_clip_or_404(job_id, clip_id)
    if not clip.file_path or not Path(clip.file_path).exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    export_dir = Path(get_config().output_dir) / "exports"
    loop = asyncio.get_event_loop()
    output_path = await loop.run_in_executor(
        None,
        export_with_preset,
        Path(clip.file_path),
        export_dir,
        preset_name,
    )

    # Persist the export path on the clip so the dedicated export download
    # route can serve the preset-encoded artifact (not the original clip).
    jobstore.update_clip(clip_id, export_path=str(output_path))

    return {
        "preset": preset_name,
        "path": str(output_path),
        "filename": output_path.name,
        "url": f"/api/jobs/{job_id}/clips/{clip_id}/export/file",
    }


@router.get("/jobs/{job_id}/clips/{clip_id}/export/file")
async def get_clip_export_file(job_id: str, clip_id: str):
    """Serve the most recent preset-encoded export for a clip as a download."""
    clip = _get_clip_or_404(job_id, clip_id)
    if not clip.export_path:
        raise HTTPException(status_code=404, detail="No export available for clip")
    export_path = Path(clip.export_path)
    if not export_path.exists():
        raise HTTPException(status_code=404, detail="Export file not found")

    return FileResponse(
        path=str(export_path),
        media_type="video/mp4",
        filename=export_path.name,
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, no-store"},
    )


# --- Selector metadata --------------------------------------------------------


@router.get("/templates")
async def get_templates():
    return {"templates": caption_templates.get_template_info()}


@router.get("/fonts")
async def get_fonts():
    return {"fonts": font_registry.get_available_fonts()}

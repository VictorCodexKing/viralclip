"""Optional B-roll fetch + overlay via the Pexels API.

Adapted from the reference ``broll.py``. Gated entirely behind
``PEXELS_API_KEY``: when the key is missing or a fetch fails, B-roll is skipped
gracefully and the pipeline continues (it never fails the render). B-roll clips,
when available, are overlaid onto the vertical clip for the model's suggested
opportunities.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_config
from .common import logger
from .ffmpeg import ffprobe_duration, run_ffmpeg_command

PEXELS_API_URL = "https://api.pexels.com/videos/search"


async def search_broll_videos(
    keyword: str,
    orientation: str = "portrait",
    per_page: int = 5,
) -> List[Dict[str, Any]]:
    """Search Pexels for relevant B-roll videos. Returns [] on any failure."""
    runtime_config = get_config()
    if not runtime_config.pexels_api_key:
        logger.info("PEXELS_API_KEY not set; skipping B-roll search")
        return []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                PEXELS_API_URL,
                params={
                    "query": keyword,
                    "orientation": orientation,
                    "size": "medium",
                    "per_page": per_page,
                },
                headers={"Authorization": runtime_config.pexels_api_key},
                timeout=30.0,
            )
            if response.status_code != 200:
                logger.warning("Pexels API error: %s", response.status_code)
                return []
            return response.json().get("videos", [])
    except Exception as exc:
        logger.warning("Error searching Pexels: %s", exc)
        return []


def get_video_download_url(
    video: Dict[str, Any], orientation: str = "portrait"
) -> Optional[str]:
    """Pick the best download URL from a Pexels video object."""
    video_files = video.get("video_files", [])
    for vf in video_files:
        if vf.get("quality") == "hd":
            width, height = vf.get("width", 0), vf.get("height", 0)
            is_portrait = height > width
            if orientation == "portrait" and is_portrait:
                return vf.get("link")
            if orientation == "landscape" and not is_portrait:
                return vf.get("link")
    for vf in video_files:
        if vf.get("quality") == "hd":
            return vf.get("link")
    return video_files[0].get("link") if video_files else None


async def download_broll_video(
    video_url: str, output_path: Path, timeout: float = 60.0
) -> bool:
    """Download a B-roll clip. Returns False on any failure."""
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                video_url, timeout=timeout, follow_redirects=True
            )
            if response.status_code != 200:
                logger.warning("Failed to download B-roll: %s", response.status_code)
                return False
            output_path.write_bytes(response.content)
            return True
    except Exception as exc:
        logger.warning("Error downloading B-roll: %s", exc)
        return False


def _parse_timestamp_seconds(timestamp_str: str) -> float:
    try:
        parts = str(timestamp_str).split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return float(timestamp_str)
    except (ValueError, IndexError):
        return 0.0


async def fetch_broll_for_opportunities(
    opportunities: List[Dict[str, Any]],
    output_dir: Path,
    orientation: str = "portrait",
) -> List[Dict[str, Any]]:
    """Fetch B-roll clips for AI-suggested opportunities. [] when unavailable."""
    if not get_config().pexels_api_key:
        logger.info("PEXELS_API_KEY not set; skipping B-roll fetch")
        return []

    broll_dir = output_dir / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)
    suggestions: List[Dict[str, Any]] = []

    for i, opp in enumerate(opportunities or []):
        keyword = opp.get("search_term", "")
        if not keyword:
            continue
        timestamp = _parse_timestamp_seconds(opp.get("timestamp", "00:00"))
        duration = float(opp.get("duration", 3.0) or 3.0)

        videos = await search_broll_videos(keyword, orientation=orientation)
        if not videos:
            continue
        video = videos[0]
        download_url = get_video_download_url(video, orientation=orientation)
        if not download_url:
            continue
        local_path = broll_dir / f"broll_{i + 1}_{video.get('id')}.mp4"
        if await download_broll_video(download_url, local_path):
            suggestions.append(
                {
                    "keyword": keyword,
                    "timestamp": float(timestamp),
                    "duration": duration,
                    "context": opp.get("context", ""),
                    "local_path": str(local_path),
                }
            )
            logger.info("B-roll %d: '%s' -> %s", i + 1, keyword, local_path)
        await asyncio.sleep(0.3)  # gentle rate-limit

    logger.info("Fetched %d B-roll clip(s)", len(suggestions))
    return suggestions


def overlay_broll(
    clip_path: Path,
    broll_suggestions: List[Dict[str, Any]],
    output_path: Path,
) -> bool:
    """Overlay downloaded B-roll clips onto a vertical clip.

    Best-effort: on any failure this returns False and the caller keeps the
    original clip. Never raises so it can't break the pipeline.
    """
    if not broll_suggestions:
        return False
    try:
        clip_duration = ffprobe_duration(clip_path)
    except Exception:
        return False

    inputs: List[str] = ["-i", str(clip_path)]
    filters: List[str] = []
    last_label = "0:v"
    valid = 0

    for idx, sug in enumerate(broll_suggestions):
        local_path = sug.get("local_path")
        if not local_path or not Path(local_path).exists():
            continue
        start = max(0.0, min(float(sug.get("timestamp", 0.0)), clip_duration - 0.5))
        dur = max(1.0, min(float(sug.get("duration", 3.0)), clip_duration - start))
        end = start + dur
        in_idx = valid + 1
        inputs += ["-i", local_path]
        scaled = f"br{idx}s"
        out = f"ov{idx}"
        filters.append(
            f"[{in_idx}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,setsar=1,trim=duration={dur:.3f},setpts=PTS-STARTPTS[{scaled}]"
        )
        filters.append(
            f"[{last_label}][{scaled}]overlay=enable='between(t,{start:.3f},{end:.3f})'[{out}]"
        )
        last_label = out
        valid += 1

    if valid == 0:
        return False

    command = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{last_label}]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        return run_ffmpeg_command(command, timeout=1800).returncode == 0
    except Exception as exc:
        logger.warning("B-roll overlay failed: %s", exc)
        return False

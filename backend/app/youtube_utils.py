"""YouTube download + source-type detection.

Adapted from the reference ``youtube_utils.py`` but reduced to the **yt-dlp**
download path only (the reference's Apify fallback and YouTube Data API metadata
provider are dropped). Adds simple source-type detection (YouTube URL vs local
uploaded file path) and a max-duration guard driven by ``config.max_video_duration``.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .config import get_config

logger = logging.getLogger("viralclip.youtube")

SOURCE_TYPE_YOUTUBE = "youtube"
SOURCE_TYPE_LOCAL = "local"

_VIDEO_SUFFIXES = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


def get_youtube_video_id(url: str) -> Optional[str]:
    """Extract a YouTube video ID from common URL formats, else None."""
    if not isinstance(url, str) or not url.strip():
        return None
    url = url.strip()
    patterns = [
        r"(?:youtube\.com/(?:.*v=|v/|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"m\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match and len(match.group(1)) == 11:
            return match.group(1)
    try:
        parsed_url = urlparse(url)
        if "youtube.com" in parsed_url.netloc.lower():
            video_ids = parse_qs(parsed_url.query).get("v")
            if video_ids and len(video_ids[0]) == 11:
                return video_ids[0]
    except Exception as exc:
        logger.warning("Error parsing URL query parameters: %s", exc)
    return None


def validate_youtube_url(url: str) -> bool:
    return get_youtube_video_id(url) is not None


def determine_source_type(url_or_path: str) -> str:
    """Classify a request source as a YouTube URL or a local uploaded file.

    Raises ``ValueError`` for anything that is neither.
    """
    if get_youtube_video_id(url_or_path):
        return SOURCE_TYPE_YOUTUBE
    candidate = Path(url_or_path)
    if candidate.suffix.lower() in _VIDEO_SUFFIXES:
        return SOURCE_TYPE_LOCAL
    raise ValueError(
        "Only YouTube URLs or local video file paths are supported as sources."
    )


def _get_optimal_download_options(video_id: str, temp_dir: Path) -> Dict[str, Any]:
    output_path = temp_dir / f"{video_id}.%(ext)s"
    return {
        "outtmpl": str(output_path),
        "format": "bestvideo*+bestaudio/best",
        "format_sort": ["res", "fps"],
        "merge_output_format": "mp4",
        "writesubtitles": False,
        "writeautomaticsub": False,
        "noplaylist": True,
        "overwrites": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "quiet": True,
        "no_warnings": False,
        "ignoreerrors": False,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        "nocheckcertificate": True,
    }


def get_youtube_video_info(url: str) -> Optional[Dict[str, Any]]:
    """Fetch lightweight metadata (incl. duration) via yt-dlp, else None."""
    video_id = get_youtube_video_id(url)
    if not video_id:
        logger.error("Invalid YouTube URL: %s", url)
        return None
    try:
        import yt_dlp

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 30,
            "nocheckcertificate": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "duration": info.get("duration"),
            "uploader": info.get("uploader"),
            "thumbnail": info.get("thumbnail"),
        }
    except Exception as exc:
        logger.warning("Failed to fetch YouTube metadata for %s: %s", url, exc)
        return None


def download_youtube_video(
    url: str, max_retries: int = 3, max_video_duration: Optional[int] = None
) -> Optional[Path]:
    """Download a YouTube video with yt-dlp; enforce a max-duration guard."""
    import yt_dlp

    video_id = get_youtube_video_id(url)
    if not video_id:
        logger.error("Could not extract video ID from URL: %s", url)
        return None

    config = get_config()
    duration_limit = (
        config.max_video_duration if max_video_duration is None else max_video_duration
    )
    temp_dir = Path(config.temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    info = get_youtube_video_info(url)
    if info:
        duration = info.get("duration") or 0
        if duration and duration > duration_limit:
            raise ValueError(
                f"Video is too long ({int(duration) // 60} min). Maximum allowed "
                f"duration is {duration_limit // 60} minutes."
            )

    for attempt in range(max_retries):
        try:
            ydl_opts = _get_optimal_download_options(video_id, temp_dir)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            downloaded = [
                p
                for p in temp_dir.glob(f"{video_id}.*")
                if p.is_file() and p.suffix.lower() in _VIDEO_SUFFIXES
            ]
            if downloaded:
                best = max(downloaded, key=lambda p: p.stat().st_size)
                logger.info("Download successful: %s", best.name)
                return best
            logger.warning("No video file found after attempt %d", attempt + 1)
        except Exception as exc:
            logger.warning("Download attempt %d failed: %s", attempt + 1, exc)
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    logger.error("All YouTube download attempts failed for %s", url)
    return None


def cleanup_downloaded_files(video_id: str) -> None:
    """Remove downloaded media, keeping the transcript-cache sidecar."""
    temp_dir = Path(get_config().temp_dir)
    for file_path in temp_dir.glob(f"{video_id}.*"):
        if file_path.name == f"{video_id}.transcript_cache.json":
            continue
        try:
            if file_path.is_file():
                file_path.unlink()
        except Exception as exc:
            logger.warning("Failed to cleanup %s: %s", file_path.name, exc)

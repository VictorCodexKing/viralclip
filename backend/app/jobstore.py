"""Single-process persistence + live-progress layer.

Replaces the reference project's Postgres + Redis stack with:

* a stdlib ``sqlite3`` store for jobs and their clips, and
* an in-process asyncio pub/sub registry so an SSE endpoint can stream live
  progress updates to browsers (this replaces Redis pub/sub).

Everything runs inside one process on a single laptop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --- Status constants ---------------------------------------------------------

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"

VALID_STATUSES = {STATUS_QUEUED, STATUS_PROCESSING, STATUS_COMPLETED, STATUS_ERROR}

# --- Database location --------------------------------------------------------

DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_DB_PATH = DATA_DIR / "viralclip.db"

_db_path: Path = DEFAULT_DB_PATH


# --- Dataclasses --------------------------------------------------------------


@dataclass
class Clip:
    id: str
    job_id: str
    filename: str | None = None
    file_path: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    duration: float | None = None
    text: str | None = None
    relevance_score: float | None = None
    reasoning: str | None = None
    virality_score: float | None = None
    hook_score: float | None = None
    engagement_score: float | None = None
    value_score: float | None = None
    shareability_score: float | None = None
    hook_type: str | None = None
    hook_title: str | None = None
    clip_order: int = 0
    export_path: str | None = None


@dataclass
class Job:
    id: str
    source_url: str | None = None
    source_type: str | None = None
    status: str = STATUS_QUEUED
    progress: int = 0
    progress_message: str | None = None
    error: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    options: dict[str, Any] = field(default_factory=dict)
    clips: list[Clip] = field(default_factory=list)


# --- Connection helpers -------------------------------------------------------


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_store(db_path: str | Path | None = None) -> None:
    """Create the database schema. Safe to call repeatedly."""
    global _db_path
    if db_path is not None:
        _db_path = Path(db_path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source_url TEXT,
                source_type TEXT,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                progress_message TEXT,
                error TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                options TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                filename TEXT,
                file_path TEXT,
                start_time REAL,
                end_time REAL,
                duration REAL,
                text TEXT,
                relevance_score REAL,
                reasoning TEXT,
                virality_score REAL,
                hook_score REAL,
                engagement_score REAL,
                value_score REAL,
                shareability_score REAL,
                hook_type TEXT,
                hook_title TEXT,
                clip_order INTEGER NOT NULL DEFAULT 0,
                export_path TEXT,
                FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_clips_job_id ON clips (job_id);
            """
        )

        # Additive migration for databases created before `export_path`
        # existed. `CREATE TABLE IF NOT EXISTS` will not add the column to an
        # already-present table, so upgrade-over-existing-DB would otherwise
        # break clip reads. Guard with a column-existence check.
        existing_clip_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(clips)").fetchall()
        }
        if "export_path" not in existing_clip_columns:
            conn.execute("ALTER TABLE clips ADD COLUMN export_path TEXT")


# --- Row mapping --------------------------------------------------------------


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source_url=row["source_url"],
        source_type=row["source_type"],
        status=row["status"],
        progress=row["progress"],
        progress_message=row["progress_message"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        options=json.loads(row["options"] or "{}"),
    )


def _row_to_clip(row: sqlite3.Row) -> Clip:
    return Clip(
        id=row["id"],
        job_id=row["job_id"],
        filename=row["filename"],
        file_path=row["file_path"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        duration=row["duration"],
        text=row["text"],
        relevance_score=row["relevance_score"],
        reasoning=row["reasoning"],
        virality_score=row["virality_score"],
        hook_score=row["hook_score"],
        engagement_score=row["engagement_score"],
        value_score=row["value_score"],
        shareability_score=row["shareability_score"],
        hook_type=row["hook_type"],
        hook_title=row["hook_title"],
        clip_order=row["clip_order"],
        export_path=row["export_path"],
    )


# --- Job CRUD -----------------------------------------------------------------


def create_job(
    source_url: str | None = None,
    source_type: str | None = None,
    options: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> Job:
    now = time.time()
    job = Job(
        id=job_id or str(uuid.uuid4()),
        source_url=source_url,
        source_type=source_type,
        status=STATUS_QUEUED,
        progress=0,
        progress_message=None,
        error=None,
        created_at=now,
        updated_at=now,
        options=options or {},
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, source_url, source_type, status, progress,
                progress_message, error, created_at, updated_at, options
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.source_url,
                job.source_type,
                job.status,
                job.progress,
                job.progress_message,
                job.error,
                job.created_at,
                job.updated_at,
                json.dumps(job.options),
            ),
        )
    return job


def get_job(job_id: str) -> Job | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = _row_to_job(row)
        job.clips = list_clips(job_id)
        return job


def list_jobs(limit: int = 50) -> list[Job]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_job(row) for row in rows]


def update_job_progress(
    job_id: str,
    progress: int | None = None,
    message: str | None = None,
    status: str | None = None,
) -> Job | None:
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")

    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None

        new_progress = row["progress"] if progress is None else int(progress)
        new_progress = max(0, min(100, new_progress))
        new_message = row["progress_message"] if message is None else message
        new_status = row["status"] if status is None else status
        now = time.time()

        conn.execute(
            """
            UPDATE jobs
            SET progress = ?, progress_message = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_progress, new_message, new_status, now, job_id),
        )

    _publish(
        job_id,
        {
            "job_id": job_id,
            "status": new_status,
            "progress": new_progress,
            "message": new_message,
        },
    )
    return get_job(job_id)


def set_job_error(job_id: str, msg: str) -> Job | None:
    with _connect() as conn:
        row = conn.execute("SELECT id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        now = time.time()
        conn.execute(
            "UPDATE jobs SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (STATUS_ERROR, msg, now, job_id),
        )

    _publish(
        job_id,
        {
            "job_id": job_id,
            "status": STATUS_ERROR,
            "error": msg,
        },
    )
    return get_job(job_id)


# --- Clip CRUD ----------------------------------------------------------------


def add_clip(
    job_id: str,
    filename: str | None = None,
    file_path: str | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    duration: float | None = None,
    text: str | None = None,
    relevance_score: float | None = None,
    reasoning: str | None = None,
    virality_score: float | None = None,
    hook_score: float | None = None,
    engagement_score: float | None = None,
    value_score: float | None = None,
    shareability_score: float | None = None,
    hook_type: str | None = None,
    hook_title: str | None = None,
    clip_order: int = 0,
    export_path: str | None = None,
    clip_id: str | None = None,
) -> Clip:
    clip = Clip(
        id=clip_id or str(uuid.uuid4()),
        job_id=job_id,
        filename=filename,
        file_path=file_path,
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        text=text,
        relevance_score=relevance_score,
        reasoning=reasoning,
        virality_score=virality_score,
        hook_score=hook_score,
        engagement_score=engagement_score,
        value_score=value_score,
        shareability_score=shareability_score,
        hook_type=hook_type,
        hook_title=hook_title,
        clip_order=clip_order,
        export_path=export_path,
    )
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO clips (
                id, job_id, filename, file_path, start_time, end_time, duration,
                text, relevance_score, reasoning, virality_score, hook_score,
                engagement_score, value_score, shareability_score, hook_type,
                hook_title, clip_order, export_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clip.id,
                clip.job_id,
                clip.filename,
                clip.file_path,
                clip.start_time,
                clip.end_time,
                clip.duration,
                clip.text,
                clip.relevance_score,
                clip.reasoning,
                clip.virality_score,
                clip.hook_score,
                clip.engagement_score,
                clip.value_score,
                clip.shareability_score,
                clip.hook_type,
                clip.hook_title,
                clip.clip_order,
                clip.export_path,
            ),
        )
    return clip


def get_clip(clip_id: str) -> Clip | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
        return _row_to_clip(row) if row is not None else None


def list_clips(job_id: str) -> list[Clip]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY clip_order, id",
            (job_id,),
        ).fetchall()
        return [_row_to_clip(row) for row in rows]


# Columns clients may update on a clip.
_UPDATABLE_CLIP_FIELDS = {
    "filename",
    "file_path",
    "start_time",
    "end_time",
    "duration",
    "text",
    "relevance_score",
    "reasoning",
    "virality_score",
    "hook_score",
    "engagement_score",
    "value_score",
    "shareability_score",
    "hook_type",
    "hook_title",
    "clip_order",
    "export_path",
}


def update_clip(clip_id: str, **fields: Any) -> Clip | None:
    updates = {k: v for k, v in fields.items() if k in _UPDATABLE_CLIP_FIELDS}
    if not updates:
        return get_clip(clip_id)

    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values())
    values.append(clip_id)

    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE clips SET {assignments} WHERE id = ?", values
        )
        if cursor.rowcount == 0:
            return None
    return get_clip(clip_id)


def delete_clip(clip_id: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
        return cursor.rowcount > 0


# --- In-process progress pub/sub ---------------------------------------------

_subscribers: dict[str, set[asyncio.Queue]] = {}
_lock = asyncio.Lock()


async def subscribe(job_id: str) -> asyncio.Queue:
    """Register a subscriber and return the queue it should read from."""
    queue: asyncio.Queue = asyncio.Queue()
    async with _lock:
        _subscribers.setdefault(job_id, set()).add(queue)
    return queue


async def unsubscribe(job_id: str, queue: asyncio.Queue) -> None:
    async with _lock:
        subscribers = _subscribers.get(job_id)
        if subscribers:
            subscribers.discard(queue)
            if not subscribers:
                _subscribers.pop(job_id, None)


def _publish(job_id: str, event: dict[str, Any]) -> None:
    """Fan an event out to every subscriber of ``job_id``.

    Non-blocking and safe to call from synchronous store functions. If there is
    no running event loop (e.g. in a plain sync test) it simply no-ops on
    delivery -- the event was already persisted to SQLite.
    """
    subscribers = _subscribers.get(job_id)
    if not subscribers:
        return

    for queue in list(subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:  # pragma: no cover - unbounded queues
            pass

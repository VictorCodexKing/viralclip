import asyncio
import importlib
import sqlite3

import pytest


def test_create_and_get_job(store):
    job = store.create_job(
        source_url="https://youtu.be/abc",
        source_type="youtube",
        options={"font": "Inter", "add_subtitles": True},
    )
    assert job.status == store.STATUS_QUEUED
    assert job.progress == 0

    fetched = store.get_job(job.id)
    assert fetched is not None
    assert fetched.source_url == "https://youtu.be/abc"
    assert fetched.source_type == "youtube"
    assert fetched.options == {"font": "Inter", "add_subtitles": True}
    assert fetched.clips == []


def test_get_missing_job_returns_none(store):
    assert store.get_job("does-not-exist") is None


def test_status_transitions(store):
    job = store.create_job(source_url="u")
    updated = store.update_job_progress(job.id, progress=30, message="Downloading", status=store.STATUS_PROCESSING)
    assert updated.status == store.STATUS_PROCESSING
    assert updated.progress == 30
    assert updated.progress_message == "Downloading"

    done = store.update_job_progress(job.id, progress=100, status=store.STATUS_COMPLETED)
    assert done.status == store.STATUS_COMPLETED
    assert done.progress == 100


def test_progress_is_clamped(store):
    job = store.create_job()
    assert store.update_job_progress(job.id, progress=999).progress == 100
    assert store.update_job_progress(job.id, progress=-5).progress == 0


def test_invalid_status_rejected(store):
    job = store.create_job()
    with pytest.raises(ValueError):
        store.update_job_progress(job.id, status="bogus")


def test_set_job_error(store):
    job = store.create_job()
    errored = store.set_job_error(job.id, "boom")
    assert errored.status == store.STATUS_ERROR
    assert errored.error == "boom"


def test_list_jobs(store):
    store.create_job(source_url="a")
    store.create_job(source_url="b")
    jobs = store.list_jobs(limit=10)
    assert len(jobs) == 2


def test_clip_crud(store):
    job = store.create_job()
    clip = store.add_clip(
        job.id,
        filename="clip1.mp4",
        start_time=1.0,
        end_time=31.0,
        duration=30.0,
        text="hello",
        virality_score=88.0,
        hook_score=22.0,
        clip_order=0,
    )
    assert store.get_clip(clip.id).virality_score == 88.0

    clips = store.list_clips(job.id)
    assert len(clips) == 1

    updated = store.update_clip(clip.id, hook_title="You won't believe this", virality_score=90.0)
    assert updated.hook_title == "You won't believe this"
    assert updated.virality_score == 90.0

    # get_job should embed clips
    job_with_clips = store.get_job(job.id)
    assert len(job_with_clips.clips) == 1

    assert store.delete_clip(clip.id) is True
    assert store.get_clip(clip.id) is None
    assert store.list_clips(job.id) == []


def test_clip_order(store):
    job = store.create_job()
    store.add_clip(job.id, filename="b.mp4", clip_order=1)
    store.add_clip(job.id, filename="a.mp4", clip_order=0)
    clips = store.list_clips(job.id)
    assert [c.filename for c in clips] == ["a.mp4", "b.mp4"]


async def test_progress_pubsub_delivers_to_subscriber(store):
    job = store.create_job()
    queue = await store.subscribe(job.id)
    try:
        store.update_job_progress(job.id, progress=50, message="Halfway", status=store.STATUS_PROCESSING)
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        await store.unsubscribe(job.id, queue)

    assert event["job_id"] == job.id
    assert event["progress"] == 50
    assert event["message"] == "Halfway"
    assert event["status"] == store.STATUS_PROCESSING


async def test_pubsub_error_event(store):
    job = store.create_job()
    queue = await store.subscribe(job.id)
    try:
        store.set_job_error(job.id, "kaboom")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        await store.unsubscribe(job.id, queue)
    assert event["status"] == store.STATUS_ERROR
    assert event["error"] == "kaboom"


def _clip_columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(clips)").fetchall()}
    finally:
        conn.close()


def test_init_store_migrates_pre_export_path_db(tmp_path):
    """A DB created before `export_path` existed must gain the column and read
    cleanly after ``init_store`` runs its guarded additive migration."""
    import app.jobstore as jobstore

    importlib.reload(jobstore)

    db_path = tmp_path / "pre_migration.db"

    # Simulate a pre-migration database: a `clips` table that predates the
    # `export_path` column, with one existing clip row and a parent job.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE jobs (
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
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE clips (
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
                clip_order INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO jobs (id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("job-old", jobstore.STATUS_COMPLETED, 0.0, 0.0),
        )
        conn.execute(
            "INSERT INTO clips (id, job_id, filename, clip_order) VALUES (?, ?, ?, ?)",
            ("clip-old", "job-old", "old.mp4", 0),
        )
        conn.commit()
    finally:
        conn.close()

    # Precondition: the column really is missing.
    assert "export_path" not in _clip_columns(db_path)

    jobstore.init_store(db_path)

    # The guarded migration must have added the column.
    assert "export_path" in _clip_columns(db_path)

    # The pre-existing row must read cleanly, with export_path defaulting to None.
    clip = jobstore.get_clip("clip-old")
    assert clip is not None
    assert clip.filename == "old.mp4"
    assert clip.export_path is None


def test_init_store_migration_is_idempotent(tmp_path):
    """Re-running ``init_store`` must not error or duplicate the column."""
    import app.jobstore as jobstore

    importlib.reload(jobstore)

    db_path = tmp_path / "idempotent.db"

    jobstore.init_store(db_path)
    columns_after_first = _clip_columns(db_path)
    assert list(columns_after_first).count("export_path") == 1

    # Calling again must be a no-op migration: no error, exactly one column.
    jobstore.init_store(db_path)
    columns_after_second = _clip_columns(db_path)
    assert columns_after_second == columns_after_first
    assert list(columns_after_second).count("export_path") == 1

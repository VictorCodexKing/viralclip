"""API route tests for the jobs router.

These exercise real routing/serialization/validation. The heavy pipeline is
mocked (monkeypatched) so no download, transcription, or ffmpeg rendering runs.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import app.jobstore as jobstore
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A TestClient wired to a fresh temp jobstore DB with a mocked pipeline."""
    # Point the store at an isolated DB file for this test.
    db_path = tmp_path / "api_test.db"
    jobstore.init_store(db_path)

    # Mock the pipeline: instead of really processing, synthesize a completed
    # job with one fake clip. We patch the symbol the router calls.
    async def fake_process_video(job_id, url_or_path, source_type=None, options=None, progress_cb=None):
        jobstore.update_job_progress(
            job_id, progress=50, message="mock working", status=jobstore.STATUS_PROCESSING
        )
        clip_file = tmp_path / f"{job_id}_clip.mp4"
        clip_file.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
        jobstore.add_clip(
            job_id=job_id,
            filename=clip_file.name,
            file_path=str(clip_file),
            start_time=0.0,
            end_time=30.0,
            duration=30.0,
            text="hello",
            virality_score=88.0,
            hook_score=22.0,
            engagement_score=22.0,
            value_score=22.0,
            shareability_score=22.0,
            hook_type="curiosity",
            hook_title="You won't believe this",
            clip_order=0,
        )
        jobstore.update_job_progress(
            job_id, progress=100, message="done", status=jobstore.STATUS_COMPLETED
        )

    import app.api.routes.jobs as jobs_module

    monkeypatch.setattr(jobs_module.pipeline, "process_video", fake_process_video)

    with TestClient(app) as test_client:
        yield test_client


def test_create_job_returns_id_and_persists_queued(client):
    resp = client.post(
        "/api/jobs",
        json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}, "options": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body

    # The job exists in the store (BackgroundTasks run after the response in
    # TestClient, and the fake pipeline drives it to completed).
    job = jobstore.get_job(body["job_id"])
    assert job is not None
    assert job.source_type == "youtube"


def test_create_job_requires_source_url(client):
    resp = client.post("/api/jobs", json={"source": {}})
    assert resp.status_code == 400


def test_create_job_rejects_unsupported_source(client):
    resp = client.post("/api/jobs", json={"source": {"url": "not-a-video"}})
    assert resp.status_code == 400


def test_get_job_returns_clips(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == job_id
    assert isinstance(body["clips"], list)
    assert len(body["clips"]) == 1
    clip = body["clips"][0]
    assert clip["virality_score"] == 88.0
    assert clip["hook_title"] == "You won't believe this"


def test_get_job_404(client):
    resp = client.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs(client):
    client.post("/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}})
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert isinstance(body["jobs"], list)


def test_templates_non_empty(client):
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    templates = resp.json()["templates"]
    assert isinstance(templates, list)
    assert len(templates) > 0
    assert "id" in templates[0]


def test_fonts_non_empty(client):
    resp = client.get("/api/fonts")
    assert resp.status_code == 200
    fonts = resp.json()["fonts"]
    assert isinstance(fonts, list)
    assert len(fonts) > 0


def test_export_rejects_unknown_preset(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]
    clip_id = jobstore.get_job(job_id).clips[0].id

    bad = client.post(
        f"/api/jobs/{job_id}/clips/{clip_id}/export", json={"preset": "myspace"}
    )
    assert bad.status_code == 400


def test_export_accepts_known_preset(client, monkeypatch):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]
    clip_id = jobstore.get_job(job_id).clips[0].id

    import app.api.routes.jobs as jobs_module

    def fake_export(input_path, output_dir, preset_name):
        from pathlib import Path

        out = Path(output_dir) / f"exported_{preset_name}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        return out

    monkeypatch.setattr(jobs_module, "export_with_preset", fake_export)

    good = client.post(
        f"/api/jobs/{job_id}/clips/{clip_id}/export", json={"preset": "tiktok"}
    )
    assert good.status_code == 200
    assert good.json()["preset"] == "tiktok"


def test_split_validates_time(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]
    clip_id = jobstore.get_job(job_id).clips[0].id

    bad = client.post(
        f"/api/jobs/{job_id}/clips/{clip_id}/split", json={"split_time": 0}
    )
    assert bad.status_code == 400

    bad2 = client.post(
        f"/api/jobs/{job_id}/clips/{clip_id}/split", json={"split_time": "abc"}
    )
    assert bad2.status_code == 400


def test_merge_validates_clip_ids(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]

    bad = client.post(f"/api/jobs/{job_id}/clips/merge", json={"clip_ids": ["only-one"]})
    assert bad.status_code == 400


def test_file_endpoint_404_for_missing_clip(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]

    missing = client.get(f"/api/jobs/{job_id}/clips/no-such-clip/file")
    assert missing.status_code == 404


def test_file_endpoint_serves_clip(client):
    resp = client.post(
        "/api/jobs", json={"source": {"url": "https://youtu.be/dQw4w9WgXcQ"}}
    )
    job_id = resp.json()["job_id"]
    clip_id = jobstore.get_job(job_id).clips[0].id

    served = client.get(f"/api/jobs/{job_id}/clips/{clip_id}/file")
    assert served.status_code == 200
    assert served.headers["content-type"] == "video/mp4"


def test_upload_rejects_non_video(client):
    resp = client.post(
        "/api/uploads",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


def test_upload_saves_video(client):
    resp = client.post(
        "/api/uploads",
        files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "local"
    assert body["source"]["url"].endswith(".mp4")
    assert body["size"] > 0


def test_sse_progress_streams_and_closes():
    """SSE yields an initial status, a progress event, then a close event.

    Driven directly against the in-process pub/sub by calling
    update_job_progress from a background task while the generator runs.
    """
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        jobstore.init_store(Path(tmp) / "sse.db")
        job = jobstore.create_job(
            source_url="https://youtu.be/x", source_type="youtube"
        )

        import app.api.routes.jobs as jobs_module

        class DummyRequest:
            async def is_disconnected(self):
                return False

        async def publish_after_delay():
            # Wait until the generator has subscribed (it subscribes right
            # after yielding the initial status event), then push a terminal
            # update through the in-process pub/sub.
            await asyncio.sleep(0.2)
            jobstore.update_job_progress(
                job.id, progress=100, message="done", status=jobstore.STATUS_COMPLETED
            )

        async def drive():
            response = await jobs_module.job_progress_sse(job.id, DummyRequest())
            gen = response.body_iterator

            events = []
            # First event: initial status.
            events.append(await gen.__anext__())

            # Start the publisher concurrently; the next __anext__ subscribes
            # then blocks on the queue until the publisher fires.
            publisher = asyncio.create_task(publish_after_delay())

            # Collect until we see the close event.
            for _ in range(8):
                events.append(await gen.__anext__())
                last = events[-1]
                event_name = last.get("event") if isinstance(last, dict) else None
                if event_name == "close":
                    break
            await publisher
            return events

        events = asyncio.run(drive())

        # We should have at least the initial status and a terminal close.
        assert len(events) >= 2
        # The final collected event should be a close on completed.
        names = [e.get("event") for e in events if isinstance(e, dict)]
        assert "close" in names

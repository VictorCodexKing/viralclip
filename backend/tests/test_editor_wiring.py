"""End-to-end editor wiring tests (trim / split / merge / export).

These use REAL ffmpeg/ffprobe on tiny generated inputs (a couple of seconds of
color + tone) so the assertions cover the actual store<->route wiring the
semantic review flagged:

* export returns a URL that serves the preset-encoded artifact (not the
  original clip), and the served bytes differ from the original,
* trim persists the recomputed duration (and shifts start/end),
* split/merge clips have a non-null duration and inherit the parent's scores.

ffmpeg/ffprobe are on PATH in this environment; inputs are kept tiny so the
real renders stay cheap.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import app.jobstore as jobstore
from app.clip_editor import ffprobe_duration
from app.config import Config, set_config_override
from app.main import app

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available on PATH",
)


def _make_clip_video(path, seconds: float, color: str = "red") -> None:
    """Render a tiny mp4 (solid color + tone) so ffprobe reports a duration."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=320x240:d={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient wired to an isolated DB and a config output_dir under tmp."""
    db_path = tmp_path / "editor_test.db"
    jobstore.init_store(db_path)

    out_dir = tmp_path / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    override = Config()
    override.output_dir = str(out_dir)
    set_config_override(override)

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        set_config_override(None)


def _seed_clip(tmp_path, seconds: float = 4.0):
    """Create a job + a real clip video and return (job_id, clip)."""
    job = jobstore.create_job(source_url="https://youtu.be/x", source_type="youtube")
    clip_path = tmp_path / f"clip_{job.id}.mp4"
    _make_clip_video(clip_path, seconds)
    clip = jobstore.add_clip(
        job_id=job.id,
        filename=clip_path.name,
        file_path=str(clip_path),
        start_time=10.0,
        end_time=10.0 + seconds,
        duration=seconds,
        text="hello world",
        relevance_score=0.9,
        reasoning="strong hook",
        virality_score=88.0,
        hook_score=22.0,
        engagement_score=22.0,
        value_score=22.0,
        shareability_score=22.0,
        hook_type="curiosity",
        hook_title="You won't believe this",
        clip_order=0,
    )
    return job.id, clip


def test_export_serves_preset_artifact_not_original(client, tmp_path):
    job_id, clip = _seed_clip(tmp_path, seconds=3.0)

    resp = client.post(
        f"/api/jobs/{job_id}/clips/{clip.id}/export", json={"preset": "tiktok"}
    )
    assert resp.status_code == 200
    body = resp.json()
    # The returned URL must point at the dedicated export route, not the file route.
    assert body["url"].endswith(f"/clips/{clip.id}/export/file")

    # The export path was persisted on the clip.
    stored = jobstore.get_clip(clip.id)
    assert stored.export_path is not None
    assert stored.export_path == body["path"]

    # Following the returned URL serves the preset-encoded artifact...
    served = client.get(body["url"])
    assert served.status_code == 200
    assert served.headers["content-type"] == "video/mp4"

    # ...and those bytes come from the export (1080x1920), not the original clip.
    original_bytes = open(clip.file_path, "rb").read()
    assert served.content != original_bytes

    export_bytes = open(stored.export_path, "rb").read()
    assert served.content == export_bytes

    # Sanity: the export really is a 1080x1920 vertical artifact.
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            stored.export_path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.strip().replace(" ", "") == "1080,1920"


def test_export_file_404_when_no_export(client, tmp_path):
    job_id, clip = _seed_clip(tmp_path, seconds=2.0)
    resp = client.get(f"/api/jobs/{job_id}/clips/{clip.id}/export/file")
    assert resp.status_code == 404


def test_trim_persists_new_duration_and_bounds(client, tmp_path):
    job_id, clip = _seed_clip(tmp_path, seconds=4.0)

    resp = client.patch(
        f"/api/jobs/{job_id}/clips/{clip.id}",
        json={"start_offset": 1.0, "end_offset": 1.0},
    )
    assert resp.status_code == 200
    updated = resp.json()["clip"]

    # Duration was recomputed and is meaningfully shorter than the 4s original.
    assert updated["duration"] is not None
    assert updated["duration"] < 4.0
    assert updated["duration"] == pytest.approx(2.0, abs=0.5)

    # start/end shifted relative to the parent's original start_time (10.0).
    assert updated["start_time"] == pytest.approx(11.0, abs=0.01)
    assert updated["end_time"] == pytest.approx(
        updated["start_time"] + updated["duration"], abs=0.01
    )

    # Persisted, not just echoed.
    stored = jobstore.get_clip(clip.id)
    assert stored.duration == updated["duration"]


def test_split_backfills_duration_and_inherits_scores(client, tmp_path):
    job_id, clip = _seed_clip(tmp_path, seconds=4.0)

    resp = client.post(
        f"/api/jobs/{job_id}/clips/{clip.id}/split", json={"split_time": 2.0}
    )
    assert resp.status_code == 200
    first, second = resp.json()["clips"]

    for part in (first, second):
        assert part["duration"] is not None
        assert part["duration"] > 0
        # Both halves inherit the parent's virality scores.
        assert part["virality_score"] == 88.0
        assert part["hook_score"] == 22.0
        assert part["hook_title"] == "You won't believe this"

    # The two halves roughly sum to the original 4s length.
    assert first["duration"] + second["duration"] == pytest.approx(4.0, abs=0.6)


def test_merge_backfills_duration_and_inherits_scores(client, tmp_path):
    job = jobstore.create_job(source_url="https://youtu.be/x", source_type="youtube")
    job_id = job.id

    clip_a_path = tmp_path / "a.mp4"
    clip_b_path = tmp_path / "b.mp4"
    _make_clip_video(clip_a_path, 2.0, color="red")
    _make_clip_video(clip_b_path, 2.0, color="blue")

    clip_a = jobstore.add_clip(
        job_id=job_id,
        filename=clip_a_path.name,
        file_path=str(clip_a_path),
        duration=2.0,
        virality_score=70.0,
        hook_score=18.0,
        engagement_score=18.0,
        value_score=17.0,
        shareability_score=17.0,
        hook_title="lower",
        clip_order=0,
    )
    clip_b = jobstore.add_clip(
        job_id=job_id,
        filename=clip_b_path.name,
        file_path=str(clip_b_path),
        duration=2.0,
        virality_score=92.0,
        hook_score=23.0,
        engagement_score=23.0,
        value_score=23.0,
        shareability_score=23.0,
        hook_title="higher",
        clip_order=1,
    )

    resp = client.post(
        f"/api/jobs/{job_id}/clips/merge",
        json={"clip_ids": [clip_a.id, clip_b.id]},
    )
    assert resp.status_code == 200
    merged = resp.json()["clip"]

    assert merged["duration"] is not None
    assert merged["duration"] == pytest.approx(4.0, abs=0.8)
    # Inherits from the strongest source clip (clip_b, 92).
    assert merged["virality_score"] == 92.0
    assert merged["hook_title"] == "higher"

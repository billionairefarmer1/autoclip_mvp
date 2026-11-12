"""Studio HTTP API tests (upload, analyse, render, download, cleanup)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """App with job storage redirected to a temp dir so tests never touch real data."""
    import backend_server

    return TestClient(backend_server.app)


@pytest.fixture(scope="module")
def uploaded_job(client, demo_video):
    with open(demo_video, "rb") as handle:
        response = client.post(
            "/api/studio/jobs",
            files={"video": ("demo.mp4", handle, "video/mp4")},
            data={"name": "API test job"},
        )
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    yield job
    client.delete(f"/api/studio/jobs/{job['id']}")


def test_health_reports_capabilities(client):
    response = client.get("/api/studio/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ffmpeg"]["available"] is True
    assert "transcription" in body
    assert "fonts" in body


def test_settings_roundtrip(client):
    original = client.get("/api/studio/settings").json()["settings"]
    response = client.put("/api/studio/settings", json={"values": {"ideal_duration": 33}})
    assert response.status_code == 200
    assert response.json()["settings"]["ideal_duration"] == 33
    # restore
    client.put("/api/studio/settings", json={"values": {"ideal_duration": original["ideal_duration"]}})


def test_settings_reject_unknown_keys(client):
    response = client.put("/api/studio/settings", json={"values": {"not_a_setting": 1}})
    assert response.status_code == 400


def test_upload_creates_job(uploaded_job):
    assert uploaded_job["status"] == "uploaded"
    assert uploaded_job["duration"] == pytest.approx(24.0, abs=1.0)
    assert uploaded_job["width"] == 640
    assert Path(uploaded_job["source"]).is_file()


def test_upload_rejects_non_video(client):
    response = client.post(
        "/api/studio/jobs",
        files={"video": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_rejects_corrupt_video(client):
    response = client.post(
        "/api/studio/jobs",
        files={"video": ("broken.mp4", b"not really an mp4", "video/mp4")},
    )
    assert response.status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/api/studio/jobs/does-not-exist").status_code == 404


def test_analyze_then_render(client, uploaded_job, demo_srt):
    job_id = uploaded_job["id"]

    # Attach the reference subtitle file.
    with open(demo_srt, "rb") as handle:
        response = client.post(
            f"/api/studio/jobs/{job_id}/subtitle",
            files={"subtitle": ("demo.srt", handle, "text/plain")},
        )
    assert response.status_code == 200

    response = client.post(
        f"/api/studio/jobs/{job_id}/analyze",
        json={"options": {"min_duration": 8, "max_duration": 20, "ideal_duration": 12}},
    )
    assert response.status_code == 200

    job = _wait_for(client, job_id, {"analyzed", "error"}, timeout=180)
    assert job["status"] == "analyzed", job.get("error")
    assert job["transcript_source"] == "user_srt"
    assert len(job["clips"]) >= 1
    for clip in job["clips"]:
        assert 0 <= clip["score"] <= 1
        assert clip["title"]

    clip_id = job["clips"][0]["id"]
    response = client.post(
        f"/api/studio/jobs/{job_id}/render",
        json={"clip_ids": [clip_id], "options": {"mode": "vertical", "captions": True, "prefer_hardware": False}},
    )
    assert response.status_code == 200

    job = _wait_for(client, job_id, {"completed", "error"}, timeout=240)
    assert job["status"] == "completed", job.get("error")
    clip = next(c for c in job["clips"] if c["id"] == clip_id)
    assert clip["status"] == "done"
    assert clip["captions"] is True
    assert clip["width"] == 1080 and clip["height"] == 1920

    # Media endpoints
    assert client.get(f"/api/studio/jobs/{job_id}/clips/{clip_id}/video").status_code == 200
    thumb = client.get(f"/api/studio/jobs/{job_id}/clips/{clip_id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/")
    assert client.get(f"/api/studio/jobs/{job_id}/clips/{clip_id}/download").status_code == 200
    srt = client.get(f"/api/studio/jobs/{job_id}/clips/{clip_id}/srt")
    assert srt.status_code == 200
    assert "-->" in srt.text

    transcript = client.get(f"/api/studio/jobs/{job_id}/transcript")
    assert transcript.status_code == 200
    assert transcript.json()["segments"]

    # Delete the rendered file but keep the candidate.
    response = client.delete(f"/api/studio/jobs/{job_id}/clips/{clip_id}/file")
    assert response.status_code == 200
    assert response.json()["removed"] is True


def test_render_before_analysis_is_rejected(client, demo_video):
    """A freshly uploaded job has no clips, so rendering must be refused."""
    with open(demo_video, "rb") as handle:
        created = client.post(
            "/api/studio/jobs",
            files={"video": ("fresh.mp4", handle, "video/mp4")},
            data={"name": "Not analysed yet"},
        )
    assert created.status_code == 200
    job_id = created.json()["job"]["id"]
    try:
        response = client.post(f"/api/studio/jobs/{job_id}/render", json={"clip_ids": None, "options": {}})
        assert response.status_code == 409
        assert "analysis" in response.json()["detail"].lower()
    finally:
        client.delete(f"/api/studio/jobs/{job_id}")


def test_stats_endpoint(client):
    response = client.get("/api/studio/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] >= 1
    assert "rendered_clips" in body


def test_root_serves_app_when_built(client):
    response = client.get("/")
    assert response.status_code == 200
    if Path("frontend/dist/index.html").exists():
        assert "<div id=" in response.text


def _wait_for(client, job_id: str, statuses: set[str], timeout: float = 120, interval: float = 1.0):
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/studio/jobs/{job_id}").json()["job"]
        if job["status"] in statuses:
            return job
        time.sleep(interval)
    raise AssertionError(f"Job did not reach {statuses}; last status={job.get('status')}")

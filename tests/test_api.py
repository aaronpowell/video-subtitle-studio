import time
from pathlib import Path

from fastapi.testclient import TestClient

from subtitle_studio.api import create_app
from subtitle_studio.config import Settings
from subtitle_studio.models import CaptionCue, CaptionProject, JobStatus


MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"


class ApiProcessor:
    def process(self, job, storage) -> None:
        storage.save_project(
            CaptionProject(
                job_id=job.id,
                source_filename=job.source_filename,
                language=job.language,
                duration_ms=10_000,
                cues=[
                    CaptionCue(
                        id="first", start_ms=1000, end_ms=2000, text="Original"
                    )
                ],
            )
        )
        storage.set_status(job.id, JobStatus.COMPLETED)


class ApiFailureProcessor:
    def process(self, job, storage) -> None:
        raise RuntimeError("test transcription failure")


def client_for(tmp_path: Path, processor, **settings) -> TestClient:
    app = create_app(Settings(data_dir=tmp_path, **settings), processor=processor)
    return TestClient(app)


def upload(client: TestClient, filename: str = "sample.mp4"):
    return client.post(
        "/api/jobs",
        files={"file": (filename, MP4, "video/mp4")},
        data={"language": "en"},
    )


def wait_for_terminal_status(client: TestClient, job_id: str) -> dict:
    for _ in range(100):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.01)
    raise AssertionError("job did not reach a terminal status")


def test_upload_edit_preview_and_exports(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = upload(client)
        assert response.status_code == 202
        job = wait_for_terminal_status(client, response.json()["id"])
        assert job["status"] == "completed"

        captions_url = f"/api/jobs/{job['id']}/captions"
        project = client.get(captions_url)
        assert project.status_code == 200
        assert project.json()["cues"][0]["text"] == "Original"

        update = {
            "cues": [
                {
                    "id": "first",
                    "start_ms": 1250,
                    "end_ms": 2500,
                    "text": "Edited",
                }
            ]
        }
        saved = client.put(captions_url, json=update)
        assert saved.status_code == 200
        assert saved.json()["cues"] == update["cues"]

        assert "Edited" in client.get(f"/api/jobs/{job['id']}/export.srt").text
        assert "Edited" in client.get(f"/api/jobs/{job['id']}/export.vtt").text
        stl = client.get(f"/api/jobs/{job['id']}/export.stl")
        assert stl.status_code == 200
        assert stl.content.startswith(b"850STL25.01")
        assert len(stl.content) == 1152
        assert client.get(f"/api/jobs/{job['id']}/media").content == MP4


def test_api_rejects_overlapping_edits(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        created = upload(client).json()
        wait_for_terminal_status(client, created["id"])
        response = client.put(
            f"/api/jobs/{created['id']}/captions",
            json={
                "cues": [
                    {"id": "a", "start_ms": 0, "end_ms": 2000, "text": "A"},
                    {"id": "b", "start_ms": 1000, "end_ms": 3000, "text": "B"},
                ]
            },
        )
        assert response.status_code == 422


def test_upload_validation_rejects_unsafe_or_non_mp4_files(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        unsafe = upload(client, "../escape.mp4")
        assert unsafe.status_code == 400
        wrong_type = client.post(
            "/api/jobs",
            files={"file": ("notes.txt", b"text", "text/plain")},
        )
        assert wrong_type.status_code == 415
        fake = client.post(
            "/api/jobs",
            files={"file": ("fake.mp4", b"not an mp4 file", "video/mp4")},
        )
        assert fake.status_code == 415


def test_request_body_limit_rejects_before_creating_a_job(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor(), max_upload_mb=1) as client:
        response = client.post(
            "/api/jobs",
            files={
                "file": (
                    "large.mp4",
                    MP4 + (b"x" * (2 * 1024 * 1024)),
                    "video/mp4",
                )
            },
        )
        assert response.status_code == 413
        assert client.get("/api/jobs").json() == []


def test_failed_processing_is_visible_through_api(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiFailureProcessor()) as client:
        response = upload(client)
        job = wait_for_terminal_status(client, response.json()["id"])
        assert job["status"] == "failed"
        assert job["error"] == "test transcription failure"
        assert client.get(f"/api/jobs/{job['id']}/captions").status_code == 409


def test_delete_completed_project_removes_job_and_files(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = upload(client)
        job = wait_for_terminal_status(client, response.json()["id"])
        job_dir = tmp_path / "jobs" / job["id"]
        assert job_dir.exists()

        deleted = client.delete(f"/api/jobs/{job['id']}")

        assert deleted.status_code == 204
        assert client.get(f"/api/jobs/{job['id']}").status_code == 404
        assert client.get(f"/api/jobs/{job['id']}/media").status_code == 404
        assert client.get("/api/jobs").json() == []
        assert not job_dir.exists()


def test_delete_rejects_active_project(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        job = client.app.state.storage.create_job("active.mp4", "en")
        client.app.state.storage.finalize_upload(job.id)
        client.app.state.storage.set_status(job.id, JobStatus.TRANSCRIBING)

        response = client.delete(f"/api/jobs/{job.id}")

        assert response.status_code == 409
        assert client.get(f"/api/jobs/{job.id}").status_code == 200


def test_delete_missing_project_returns_not_found(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.delete("/api/jobs/00000000-0000-0000-0000-000000000000")

        assert response.status_code == 404


def test_health_and_missing_job(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/jobs/not-a-job").status_code == 404

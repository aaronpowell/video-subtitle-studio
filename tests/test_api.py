import io
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from subtitle_studio.api import create_app
from subtitle_studio.config import Settings
from subtitle_studio.models import CaptionCue, CaptionProject, JobStatus


MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 413
MP3_WITHOUT_ID3 = MP3[10:]


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(44100)
        audio.writeframes(b"\x00" * 44100 * 4)
    return output.getvalue()


WAV = wav_bytes()


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


@pytest.mark.parametrize(
    ("filename", "body", "content_type"),
    [
        ("sample.mp4", MP4, "video/mp4"),
        ("sample.wav", WAV, "audio/wav"),
        ("sample.mp3", MP3, "audio/mpeg"),
        ("frames.MP3", MP3_WITHOUT_ID3, "audio/mpeg"),
    ],
    ids=["mp4", "wav", "mp3-id3", "mp3-frames"],
)
def test_upload_edit_preview_and_exports(
    tmp_path: Path, filename: str, body: bytes, content_type: str
) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.post(
            "/api/jobs", files={"file": (filename, body, content_type)}
        )
        assert response.status_code == 202
        job = wait_for_terminal_status(client, response.json()["id"])
        assert job["status"] == "completed"
        assert job["source_filename"] == filename
        stored_job = client.app.state.storage.get_job(job["id"])
        assert stored_job.source_path.name == f"source{Path(filename).suffix.lower()}"
        assert stored_job.source_path.read_bytes() == body

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
        media = client.get(f"/api/jobs/{job['id']}/media")
        assert media.content == body
        assert media.headers["content-type"] == content_type
        assert Path(filename).suffix.lower() in media.headers["content-disposition"]
        ranged = client.get(
            f"/api/jobs/{job['id']}/media", headers={"Range": "bytes=0-11"}
        )
        assert ranged.status_code == 206
        assert ranged.content == body[:12]


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


def test_upload_validation_rejects_unsafe_or_unsupported_files(tmp_path: Path) -> None:
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


@pytest.mark.parametrize(
    ("filename", "body", "content_type"),
    [
        ("sample.WAV", WAV, "audio/x-wav"),
        ("sample.wav", WAV, "audio/wave"),
        ("sample.wav", WAV, "audio/vnd.wave"),
        ("sample.wav", WAV, "application/octet-stream"),
        ("sample.mp3", MP3, "audio/mp3"),
        ("sample.mp3", MP3_WITHOUT_ID3, "audio/x-mp3"),
        ("sample.mp3", MP3, "application/octet-stream"),
    ],
    ids=["x-wav", "wave", "vnd-wave", "wav-binary", "mp3", "x-mp3", "mp3-binary"],
)
def test_audio_upload_accepts_common_content_types(
    tmp_path: Path, filename: str, body: bytes, content_type: str
) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.post(
            "/api/jobs", files={"file": (filename, body, content_type)}
        )
        assert response.status_code == 202
        job = wait_for_terminal_status(client, response.json()["id"])
        assert job["status"] == "completed"


@pytest.mark.parametrize(
    ("filename", "body", "content_type", "status_code"),
    [
        ("fake.wav", b"not a wav file", "audio/wav", 415),
        ("fake.wav", b"RIFF\x00\x00\x00\x00AVI ", "audio/wav", 415),
        ("fake.mp3", b"not an mp3 file", "audio/mpeg", 415),
        ("fake.mp3", b"\xff\xff\xff\xff" * 3, "audio/mpeg", 415),
        ("fake.mp3", b"\xff\xfb\xfc\x00" * 3, "audio/mpeg", 415),
        ("fake.mp3", WAV, "audio/mpeg", 415),
        ("fake.wav", MP3, "audio/wav", 415),
        ("wrong.wav", WAV, "audio/mpeg", 415),
        ("wrong.mp3", MP3, "video/mp4", 415),
        ("empty.wav", b"", "audio/wav", 400),
        ("short.mp3", b"ID3", "audio/mpeg", 400),
    ],
    ids=[
        "fake-wav", "avi", "fake-mp3", "invalid-frame", "invalid-sample-rate",
        "wav-as-mp3", "mp3-as-wav", "wav-mime", "mp3-mime", "empty", "truncated",
    ],
)
def test_invalid_audio_upload_leaves_no_job_or_files(
    tmp_path: Path, filename: str, body: bytes, content_type: str, status_code: int
) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.post(
            "/api/jobs", files={"file": (filename, body, content_type)}
        )
        assert response.status_code == status_code
        assert client.get("/api/jobs").json() == []
        assert list((tmp_path / "jobs").iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "body", "content_type"),
    [
        ("large.mp4", MP4, "video/mp4"),
        ("large.wav", WAV, "audio/wav"),
        ("large.mp3", MP3, "audio/mpeg"),
    ],
    ids=["mp4", "wav", "mp3"],
)
def test_audio_and_video_file_size_limits(
    tmp_path: Path, filename: str, body: bytes, content_type: str
) -> None:
    with client_for(tmp_path, ApiProcessor(), max_upload_mb=1) as client:
        response = client.post(
            "/api/jobs",
            files={"file": (filename, body + b"x" * (1024 * 1024), content_type)},
        )
        assert response.status_code == 413
        assert client.get("/api/jobs").json() == []
        assert list((tmp_path / "jobs").iterdir()) == []


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


def test_reformat_presets_endpoint_exposes_style_defaults(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.get("/api/reformat-presets")
        assert response.status_code == 200
        presets = response.json()
        assert presets["vertical"]["words_per_block"] == 2
        assert presets["vertical"]["remove_punctuation"] is True
        assert presets["landscape"]["words_per_block"] is None


def test_reformat_applies_preset_and_persists_new_cues(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        created = upload(client).json()
        wait_for_terminal_status(client, created["id"])

        response = client.post(
            f"/api/jobs/{created['id']}/reformat",
            json={"style": "vertical"},
        )

        assert response.status_code == 200
        project = response.json()
        assert all(len(cue["text"].split()) <= 2 for cue in project["cues"])

        persisted = client.get(f"/api/jobs/{created['id']}/captions").json()
        assert persisted["cues"] == project["cues"]


def test_reformat_overrides_take_precedence_over_style(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        created = upload(client).json()
        wait_for_terminal_status(client, created["id"])

        response = client.post(
            f"/api/jobs/{created['id']}/reformat",
            json={"style": "vertical", "remove_punctuation": False, "casing": "upper"},
        )

        assert response.status_code == 200
        project = response.json()
        assert project["cues"][0]["text"] == project["cues"][0]["text"].upper()


def test_reformat_missing_job_returns_not_found(tmp_path: Path) -> None:
    with client_for(tmp_path, ApiProcessor()) as client:
        response = client.post(
            "/api/jobs/00000000-0000-0000-0000-000000000000/reformat",
            json={"style": "square"},
        )
        assert response.status_code == 404

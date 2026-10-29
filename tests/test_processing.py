import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from subtitle_studio.models import JobStatus, TranscriptSegment
from subtitle_studio.processing import MediaProcessor
from subtitle_studio.storage import Storage
from subtitle_studio.worker import JobWorker


class CheckingTranscriber:
    def transcribe(self, audio_path: Path, language: str) -> list[TranscriptSegment]:
        assert language == "en"
        with wave.open(str(audio_path), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 16000
        return [TranscriptSegment(start_ms=0, end_ms=500, text="Audio caption")]


@pytest.mark.parametrize("extension", [".mp4", ".wav", ".mp3"])
def test_processor_normalizes_all_inputs_and_preserves_original(
    tmp_path: Path, monkeypatch, extension: str
) -> None:
    storage = Storage(tmp_path)
    job = storage.create_job(f"recording{extension}", "en")
    job.source_path.write_bytes(b"original upload")
    storage.finalize_upload(job.id)

    def fake_ffmpeg(command, **kwargs):
        assert command[command.index("-i") + 1] == str(job.source_path)
        assert command[command.index("-ac") + 1] == "1"
        assert command[command.index("-ar") + 1] == "16000"
        assert command[command.index("-c:a") + 1] == "pcm_s16le"
        assert kwargs["timeout"] == 123
        destination = Path(command[-1])
        assert destination == storage.job_dir(job.id) / "audio.wav"
        assert destination != job.source_path
        with wave.open(str(destination), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 16000)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_ffmpeg)
    processor = MediaProcessor(CheckingTranscriber(), ffmpeg_timeout_seconds=123)
    processor.process(job, storage)

    assert storage.get_job(job.id).status == JobStatus.COMPLETED
    assert job.source_path.read_bytes() == b"original upload"
    project = storage.get_project(job.id)
    assert project.source_filename == f"recording{extension}"
    assert project.duration_ms == 1000
    assert project.cues[0].text == "Audio caption"


def test_audio_decode_failure_is_recorded(tmp_path: Path, monkeypatch) -> None:
    storage = Storage(tmp_path)
    job = storage.create_job("corrupt.mp3", "en")
    job.source_path.write_bytes(b"ID3 but not decodable")
    storage.finalize_upload(job.id)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stderr="Invalid audio data"
        ),
    )
    worker = JobWorker(storage, MediaProcessor(CheckingTranscriber()))
    worker.start()
    worker.enqueue(job.id)
    worker.wait_until_idle()
    worker.stop()

    failed = storage.get_job(job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error == "FFmpeg could not decode the upload: Invalid audio data"

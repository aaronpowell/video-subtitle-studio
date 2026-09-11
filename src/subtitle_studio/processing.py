import subprocess
import wave
from pathlib import Path
from typing import Protocol

from .models import CaptionCue, CaptionProject, JobStatus, StoredJob
from .storage import Storage
from .transcription import Transcriber


class JobProcessor(Protocol):
    def process(self, job: StoredJob, storage: Storage) -> None:
        ...


class MediaProcessor:
    def __init__(
        self,
        transcriber: Transcriber,
        ffmpeg_path: str = "ffmpeg",
        ffmpeg_timeout_seconds: int = 7200,
    ):
        self.transcriber = transcriber
        self.ffmpeg_path = ffmpeg_path
        self.ffmpeg_timeout_seconds = ffmpeg_timeout_seconds

    def process(self, job: StoredJob, storage: Storage) -> None:
        audio_path = storage.job_dir(job.id) / "audio.wav"
        storage.set_status(job.id, JobStatus.EXTRACTING)
        self._extract_audio(job.source_path, audio_path)
        storage.set_status(job.id, JobStatus.TRANSCRIBING)
        segments = self.transcriber.transcribe(audio_path, job.language)
        duration_ms = self._wav_duration_ms(audio_path)
        cues = [
            CaptionCue(start_ms=item.start_ms, end_ms=item.end_ms, text=item.text)
            for item in segments
        ]
        storage.save_project(
            CaptionProject(
                job_id=job.id,
                source_filename=job.source_filename,
                language=job.language,
                duration_ms=duration_ms,
                cues=cues,
            )
        )
        storage.set_status(job.id, JobStatus.COMPLETED)

    def close(self) -> None:
        close = getattr(self.transcriber, "close", None)
        if close is not None:
            close()

    def _extract_audio(self, source_path: Path, destination: Path) -> None:
        command = [
            self.ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.ffmpeg_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as error:
            raise RuntimeError(f"FFmpeg executable not found: {self.ffmpeg_path}") from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("FFmpeg audio extraction timed out") from error
        if result.returncode != 0:
            detail = result.stderr.strip()[-2000:] or "unknown FFmpeg error"
            raise RuntimeError(f"FFmpeg could not decode the upload: {detail}")

    @staticmethod
    def _wav_duration_ms(audio_path: Path) -> int:
        with wave.open(str(audio_path), "rb") as audio:
            return round(audio.getnframes() * 1000 / audio.getframerate())

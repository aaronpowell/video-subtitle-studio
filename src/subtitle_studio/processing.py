import re
import subprocess
import wave
from pathlib import Path
from typing import Protocol

from .models import (
    CaptionCue,
    CaptionProject,
    JobStatus,
    ReformatRequest,
    ReformatSettings,
    StoredJob,
    VideoStyle,
    WordCasing,
)
from .storage import Storage
from .transcription import Transcriber

#: Sensible defaults per target video style, used when a reformat request
#: names a `style` instead of (or alongside) explicit overrides.
REFORMAT_PRESETS: dict[VideoStyle, ReformatSettings] = {
    VideoStyle.LANDSCAPE: ReformatSettings(
        words_per_block=None, remove_punctuation=False, casing=WordCasing.DEFAULT
    ),
    VideoStyle.SQUARE: ReformatSettings(
        words_per_block=5, remove_punctuation=False, casing=WordCasing.DEFAULT
    ),
    VideoStyle.VERTICAL: ReformatSettings(
        words_per_block=2, remove_punctuation=True, casing=WordCasing.DEFAULT
    ),
}

_PUNCTUATION_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.UNICODE)


def resolve_reformat_settings(request: ReformatRequest) -> ReformatSettings:
    """Merge a named style preset with any explicit field overrides."""
    base = REFORMAT_PRESETS[request.style] if request.style else ReformatSettings()
    return ReformatSettings(
        words_per_block=(
            request.words_per_block
            if request.words_per_block is not None
            else base.words_per_block
        ),
        remove_punctuation=(
            request.remove_punctuation
            if request.remove_punctuation is not None
            else base.remove_punctuation
        ),
        casing=request.casing if request.casing is not None else base.casing,
    )


def estimate_word_timings(
    text: str, start_ms: int, end_ms: int
) -> list[tuple[str, int, int]]:
    """Estimate per-word start/end times within a phrase-level cue.

    The transcriber only provides phrase-level timing, so each word's
    duration is approximated proportionally to its character length.
    """
    words = text.split()
    if not words:
        return []
    duration = end_ms - start_ms
    weights = [len(word) for word in words]
    total_weight = sum(weights) or len(words)
    cursor = start_ms
    cumulative = 0.0
    timings: list[tuple[str, int, int]] = []
    for index, word in enumerate(words):
        cumulative += weights[index] / total_weight * duration
        word_end = end_ms if index == len(words) - 1 else start_ms + round(cumulative)
        word_end = max(word_end, cursor + 1)
        timings.append((word, cursor, word_end))
        cursor = word_end
    return timings


def _strip_word_punctuation(word: str) -> str:
    # Only trims leading/trailing punctuation; punctuation embedded without
    # surrounding whitespace (e.g. contractions, em-dashes joining words) is
    # preserved rather than guessed at.
    return _PUNCTUATION_EDGE.sub("", word)


def _apply_casing(text: str, casing: WordCasing) -> str:
    if casing is WordCasing.UPPER:
        return text.upper()
    if casing is WordCasing.LOWER:
        return text.lower()
    return text


def reformat_cues(
    cues: list[CaptionCue], settings: ReformatSettings
) -> list[CaptionCue]:
    """Rebuild cues as word-count-limited blocks with punctuation/casing applied.

    Each source cue's words are re-timed with `estimate_word_timings` and
    regrouped into blocks of at most `words_per_block` words (or left as a
    single block when unset). Blocks are never merged across source cues, so
    natural pauses in the original transcript are preserved.
    """
    new_cues: list[CaptionCue] = []
    for cue in cues:
        words = estimate_word_timings(cue.text, cue.start_ms, cue.end_ms)
        if settings.remove_punctuation:
            words = [
                (stripped, start, end)
                for word, start, end in words
                if (stripped := _strip_word_punctuation(word))
            ]
        if not words:
            continue
        block_size = settings.words_per_block or len(words)
        for offset in range(0, len(words), block_size):
            block = words[offset : offset + block_size]
            text = _apply_casing(" ".join(word for word, _, _ in block), settings.casing)
            new_cues.append(
                CaptionCue(start_ms=block[0][1], end_ms=block[-1][2], text=text)
            )
    return new_cues


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

from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path
from typing import Protocol

from .models import TranscriptSegment


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: str) -> list[TranscriptSegment]:
        ...


class MoonshineTranscriber:
    def __init__(self, model_arch: str | None = None, chunk_seconds: float = 0.1):
        self.model_arch = model_arch
        self.chunk_seconds = chunk_seconds
        self._engine_language: str | None = None
        self._engine: object | None = None

    def transcribe(self, audio_path: Path, language: str) -> list[TranscriptSegment]:
        engine = self._engine_for(language)
        stream = engine.create_stream(update_interval=0.5)
        with wave.open(str(audio_path), "rb") as audio:
            if (
                audio.getnchannels() != 1
                or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE"
            ):
                raise ValueError("Moonshine input must be mono 16-bit PCM WAV")
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            frames_per_chunk = max(1, int(sample_rate * self.chunk_seconds))
            stream.start()
            try:
                while pcm_bytes := audio.readframes(frames_per_chunk):
                    samples = array("h")
                    samples.frombytes(pcm_bytes)
                    if sys.byteorder != "little":
                        samples.byteswap()
                    stream.add_audio(
                        [sample / 32768.0 for sample in samples],
                        sample_rate,
                    )
                transcript = stream.stop()
            finally:
                stream.close()

        duration_ms = round(frame_count * 1000 / sample_rate)
        raw: list[tuple[int, int, str]] = []
        for line in transcript.lines:
            if not line.is_complete:
                continue
            text = str(getattr(line, "text", "")).strip()
            if not text:
                continue
            start_ms = round(float(line.start_time) * 1000)
            end_ms = round((float(line.start_time) + float(line.duration)) * 1000)
            raw.append((start_ms, end_ms, text))

        raw.sort(key=lambda item: item[0])
        coalesced: list[tuple[int, int, str]] = []
        for start_ms, end_ms, text in raw:
            if coalesced and coalesced[-1][0] == start_ms:
                previous_start, previous_end, previous_text = coalesced[-1]
                coalesced[-1] = (
                    previous_start,
                    max(previous_end, end_ms),
                    f"{previous_text}\n{text}",
                )
            else:
                coalesced.append((start_ms, end_ms, text))

        segments: list[TranscriptSegment] = []
        for index, (start_ms, end_ms, text) in enumerate(coalesced):
            next_start = (
                coalesced[index + 1][0]
                if index + 1 < len(coalesced)
                else max(duration_ms, start_ms + 1)
            )
            resolved_end = min(max(end_ms, start_ms + 1), duration_ms)
            if index + 1 < len(coalesced):
                resolved_end = min(resolved_end, next_start)
            resolved_end = max(resolved_end, start_ms + 1)
            segments.append(
                TranscriptSegment(start_ms=start_ms, end_ms=resolved_end, text=text)
            )
        return segments

    def close(self) -> None:
        if self._engine is not None:
            self._engine.close()
        self._engine = None
        self._engine_language = None

    def _engine_for(self, language: str):
        from moonshine_voice import ModelArch, Transcriber as MoonshineEngine, get_model_for_language

        if self._engine is not None and self._engine_language == language:
            return self._engine
        self.close()

        if self.model_arch:
            normalized = self.model_arch.strip().upper().replace("-", "_")
            try:
                requested_arch = ModelArch[normalized]
            except KeyError as error:
                supported = ", ".join(item.name.lower() for item in ModelArch)
                raise ValueError(
                    f"Unknown Moonshine model architecture {self.model_arch!r}; "
                    f"choose one of: {supported}"
                ) from error
            model_path, model_arch = get_model_for_language(language, requested_arch)
        else:
            model_path, model_arch = get_model_for_language(language)
        engine = MoonshineEngine(
            model_path=model_path,
            model_arch=model_arch,
            update_interval=0.5,
            options={"return_audio_data": False},
        )
        self._engine = engine
        self._engine_language = language
        return engine

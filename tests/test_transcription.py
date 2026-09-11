import sys
import wave
from pathlib import Path
from types import SimpleNamespace

from subtitle_studio.transcription import MoonshineTranscriber


class FakeModelArch:
    TINY = "tiny"
    BASE = "base"
    TINY_STREAMING = "tiny_streaming"
    BASE_STREAMING = "base_streaming"
    SMALL_STREAMING = "small_streaming"
    MEDIUM_STREAMING = "medium_streaming"

    @classmethod
    def __class_getitem__(cls, name: str):
        return getattr(cls, name)

    def __iter__(self):
        return iter(())


class FakeStream:
    def __init__(self):
        self.started = False
        self.closed = False
        self.chunks = []

    def start(self):
        self.started = True

    def add_audio(self, audio, sample_rate):
        self.chunks.append((list(audio), sample_rate))

    def stop(self):
        return SimpleNamespace(
            lines=[
                SimpleNamespace(
                    text=" First ", start_time=0.25, duration=1.0, is_complete=True
                ),
                SimpleNamespace(
                    text="Second", start_time=1.2, duration=0.5, is_complete=True
                ),
                SimpleNamespace(
                    text="draft", start_time=1.8, duration=0.1, is_complete=False
                ),
            ]
        )

    def close(self):
        self.closed = True


class FakeEngine:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.streams = []
        self.closed = False
        self.instances.append(self)

    def create_stream(self, **kwargs):
        stream = FakeStream()
        self.streams.append((stream, kwargs))
        return stream

    def close(self):
        self.closed = True


def fake_module():
    return SimpleNamespace(
        ModelArch=FakeModelArch,
        Transcriber=FakeEngine,
        get_model_for_language=lambda language, *args: (f"/models/{language}", args[0] if args else "default"),
    )


def test_current_moonshine_api_preserves_line_start_and_duration(
    monkeypatch, tmp_path: Path
) -> None:
    FakeEngine.instances.clear()
    monkeypatch.setitem(sys.modules, "moonshine_voice", fake_module())
    audio_path = tmp_path / "audio.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 32000)
    transcriber = MoonshineTranscriber(chunk_seconds=0.1)
    segments = transcriber.transcribe(audio_path, "en")
    assert [segment.model_dump() for segment in segments] == [
        {"start_ms": 250, "end_ms": 1200, "text": "First"},
        {"start_ms": 1200, "end_ms": 1700, "text": "Second"},
    ]
    assert len(FakeEngine.instances[0].streams[0][0].chunks) == 20
    assert FakeEngine.instances[0].kwargs["options"] == {"return_audio_data": False}

    transcriber.transcribe(audio_path, "en")
    assert len(FakeEngine.instances) == 1
    transcriber.transcribe(audio_path, "es")
    assert len(FakeEngine.instances) == 2
    assert FakeEngine.instances[0].closed
    transcriber.close()
    assert FakeEngine.instances[1].closed

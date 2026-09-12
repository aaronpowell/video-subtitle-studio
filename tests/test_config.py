import pytest
from pydantic import ValidationError

from subtitle_studio.config import Settings


def test_ebu_frame_rate_accepts_env_string(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VSS_EBU_FRAME_RATE", "25")

    settings = Settings(_env_file=None)

    assert settings.ebu_frame_rate == 25


def test_ebu_frame_rate_rejects_unsupported_values() -> None:
    with pytest.raises(ValidationError, match="25 or 30"):
        Settings(ebu_frame_rate=24)

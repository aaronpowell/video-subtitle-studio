from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VSS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("./data")
    max_upload_mb: int = Field(default=2048, ge=1, le=102400)
    ffmpeg_path: str = "ffmpeg"
    ffmpeg_timeout_seconds: int = Field(default=7200, ge=30)
    moonshine_language: str = Field(default="en", min_length=2, max_length=16)
    moonshine_model_arch: str | None = None
    transcription_chunk_seconds: float = Field(default=0.1, ge=0.02, le=2.0)
    ebu_frame_rate: int = 25
    ebu_chars_per_line: int = Field(default=40, ge=10, le=80)
    ebu_max_lines: int = Field(default=2, ge=1, le=23)

    @field_validator("ebu_frame_rate")
    @classmethod
    def validate_ebu_frame_rate(cls, value: int) -> int:
        if value not in {25, 30}:
            raise ValueError("EBU STL export supports 25 or 30 fps")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

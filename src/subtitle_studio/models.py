from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    EXTRACTING = "extracting"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    FAILED = "failed"


class CaptionCue(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=128)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_range_and_text(self) -> Self:
        if self.end_ms <= self.start_ms:
            raise ValueError("end_ms must be greater than start_ms")
        if "\x00" in self.text:
            raise ValueError("caption text cannot contain NUL characters")
        return self


class CaptionProject(BaseModel):
    job_id: str
    source_filename: str
    language: str
    duration_ms: int = Field(ge=0)
    cues: list[CaptionCue] = Field(max_length=10000)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_cues(self) -> Self:
        ids: set[str] = set()
        previous_end = 0
        for cue in self.cues:
            if cue.id in ids:
                raise ValueError(f"duplicate cue id: {cue.id}")
            ids.add(cue.id)
            if cue.start_ms < previous_end:
                raise ValueError("cues must be ordered and cannot overlap")
            if self.duration_ms and cue.end_ms > self.duration_ms + 1000:
                raise ValueError("caption extends beyond the media duration")
            previous_end = cue.end_ms
        return self


class CaptionUpdate(BaseModel):
    cues: list[CaptionCue] = Field(max_length=10000)


class Job(BaseModel):
    id: str
    source_filename: str
    language: str
    status: JobStatus
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class StoredJob(Job):
    source_path: Path


class TranscriptSegment(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

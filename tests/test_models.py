import pytest
from pydantic import ValidationError

from subtitle_studio.models import CaptionCue, CaptionProject


def project_with(cues: list[CaptionCue]) -> CaptionProject:
    return CaptionProject(
        job_id="job",
        source_filename="video.mp4",
        language="en",
        duration_ms=10_000,
        cues=cues,
    )


def test_cue_requires_positive_duration() -> None:
    with pytest.raises(ValidationError, match="greater than start_ms"):
        CaptionCue(start_ms=1000, end_ms=1000, text="No duration")


def test_project_rejects_overlapping_cues() -> None:
    with pytest.raises(ValidationError, match="cannot overlap"):
        project_with(
            [
                CaptionCue(start_ms=0, end_ms=2000, text="One"),
                CaptionCue(start_ms=1500, end_ms=3000, text="Two"),
            ]
        )


def test_project_rejects_duplicate_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate cue id"):
        project_with(
            [
                CaptionCue(id="same", start_ms=0, end_ms=1000, text="One"),
                CaptionCue(id="same", start_ms=1000, end_ms=2000, text="Two"),
            ]
        )


def test_project_allows_ordered_non_overlapping_cues() -> None:
    project = project_with(
        [
            CaptionCue(start_ms=0, end_ms=1000, text="One"),
            CaptionCue(start_ms=1000, end_ms=2000, text="Two"),
        ]
    )
    assert len(project.cues) == 2


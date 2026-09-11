from datetime import datetime, timezone

import pytest

from subtitle_studio.exporters import (
    export_ebu_stl,
    export_srt,
    export_webvtt,
    milliseconds_to_timecode,
)
from subtitle_studio.models import CaptionCue, CaptionProject


@pytest.fixture
def project() -> CaptionProject:
    return CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=10_000,
        cues=[
            CaptionCue(id="a", start_ms=1000, end_ms=2250, text="Hello"),
            CaptionCue(id="b", start_ms=3000, end_ms=4501, text="Second line"),
        ],
    )


def test_srt_formats_ordered_cues(project: CaptionProject) -> None:
    assert export_srt(project).decode() == (
        "1\r\n00:00:01,000 --> 00:00:02,250\r\nHello\r\n\r\n"
        "2\r\n00:00:03,000 --> 00:00:04,501\r\nSecond line\r\n"
    )


def test_webvtt_uses_project_cue_ids(project: CaptionProject) -> None:
    assert export_webvtt(project).decode() == (
        "WEBVTT\n\n"
        "a\n00:00:01.000 --> 00:00:02.250\nHello\n\n"
        "b\n00:00:03.000 --> 00:00:04.501\nSecond line\n"
    )


def test_timecode_conversion_uses_floor_for_in_and_can_ceil_out() -> None:
    assert milliseconds_to_timecode(1250, 25) == (0, 0, 1, 6)
    assert milliseconds_to_timecode(1250, 25, ceil=True) == (0, 0, 1, 7)
    assert milliseconds_to_timecode(1000, 25, ceil=True) == (0, 0, 1, 0)
    with pytest.raises(ValueError, match="less than 24 hours"):
        milliseconds_to_timecode(24 * 60 * 60 * 1000, 25)
    with pytest.raises(ValueError, match="25 or 30"):
        milliseconds_to_timecode(1000, 24)


def test_ebu_stl_has_gsi_and_binary_tti_blocks(project: CaptionProject) -> None:
    result = export_ebu_stl(
        project,
        frame_rate=25,
        creation_time=datetime(2026, 9, 11, tzinfo=timezone.utc),
    )
    assert len(result) == 1024 + (2 * 128)
    assert result[0:3] == b"850"
    assert result[3:11] == b"STL25.01"
    assert result[224:230] == b"260911"
    assert result[238:243] == b"00002"
    assert result[243:248] == b"00002"
    assert result[251:253] == b"40"
    assert result[256:264] == b"00000000"
    assert result[264:272] == b"00000100"
    assert result[373:448] == b" " * 75

    first_tti = result[1024:1152]
    assert first_tti[0] == 0
    assert int.from_bytes(first_tti[1:3], "little") == 1
    assert first_tti[3] == 0xFF
    assert first_tti[5:9] == bytes([0, 0, 1, 0])
    assert first_tti[9:13] == bytes([0, 0, 2, 7])
    assert first_tti[16:21] == b"Hello"
    assert first_tti[-1] == 0x8F


def test_ebu_stl_wraps_text_and_uses_newline_control_code() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="one two three four")],
    )
    result = export_ebu_stl(project, chars_per_line=10, max_lines=2)
    text_field = result[1040:1152]
    assert text_field.startswith(b"one two\x8athree four")


def test_ebu_stl_rejects_unrepresentable_characters(project: CaptionProject) -> None:
    project.cues[0].text = "Not ISO 6937: \U0001f680"
    with pytest.raises(UnicodeEncodeError):
        export_ebu_stl(project)


def test_ebu_stl_encodes_latin_diacritics_as_iso_6937() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="Caf\u00e9")],
    )
    result = export_ebu_stl(project)
    assert result[1040:1045] == b"Caf\xc2e"


def test_ebu_stl_rejects_undefined_diacritic_combinations() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="\u1e3f")],
    )
    with pytest.raises(UnicodeEncodeError):
        export_ebu_stl(project)


def test_ebu_stl_terminates_an_exact_length_text_field() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[
            CaptionCue(
                start_ms=0,
                end_ms=1000,
                text=("A" * 80) + " " + ("B" * 31),
            )
        ],
    )
    result = export_ebu_stl(project, chars_per_line=80, max_lines=2)
    assert len(result) == 1024 + 256
    assert result[1027] == 0
    assert result[1155] == 0xFF
    assert result[1168] == 0x8F


def test_ebu_stl_declares_configured_line_width() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="Caption")],
    )
    assert export_ebu_stl(project, chars_per_line=80)[251:253] == b"80"


def test_ebu_stl_quantizes_adjacent_cues_without_overlap() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[
            CaptionCue(start_ms=0, end_ms=1250, text="One"),
            CaptionCue(start_ms=1250, end_ms=2500, text="Two"),
        ],
    )
    result = export_ebu_stl(project, frame_rate=25)
    first_out = result[1033:1037]
    second_in = result[1157:1161]
    assert first_out == bytes([0, 0, 1, 7])
    assert second_in == first_out


def test_ebu_stl_sets_supported_language_metadata() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="es",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="Hola")],
    )
    assert export_ebu_stl(project)[14:16] == b"0A"

    unsupported = project.model_copy(update={"language": "fr"})
    with pytest.raises(ValueError, match="currently supports"):
        export_ebu_stl(unsupported)


def test_ebu_stl_rejects_more_than_configured_lines() -> None:
    project = CaptionProject(
        job_id="job",
        source_filename="example.mp4",
        language="en",
        duration_ms=5000,
        cues=[CaptionCue(start_ms=0, end_ms=1000, text="one two three four five")],
    )
    with pytest.raises(ValueError, match="configured for 2"):
        export_ebu_stl(project, chars_per_line=5, max_lines=2)

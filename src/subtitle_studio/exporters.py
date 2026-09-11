import math
import struct
import textwrap
import unicodedata
from datetime import datetime, timezone

from .models import CaptionCue, CaptionProject

_EBU_LANGUAGE_CODES = {
    "de": "08",
    "en": "09",
    "es": "0A",
}


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _vtt_timestamp(milliseconds: int) -> str:
    return _srt_timestamp(milliseconds).replace(",", ".")


def export_srt(project: CaptionProject) -> bytes:
    entries = [
        f"{index}\r\n{_srt_timestamp(cue.start_ms)} --> {_srt_timestamp(cue.end_ms)}\r\n"
        + cue.text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
        for index, cue in enumerate(project.cues, start=1)
    ]
    return ("\r\n\r\n".join(entries) + ("\r\n" if entries else "")).encode("utf-8")


def export_webvtt(project: CaptionProject) -> bytes:
    entries = [
        f"{cue.id}\n{_vtt_timestamp(cue.start_ms)} --> {_vtt_timestamp(cue.end_ms)}\n{cue.text}"
        for cue in project.cues
    ]
    body = "\n\n".join(entries)
    return ("WEBVTT\n\n" + body + ("\n" if body else "")).encode("utf-8")


def _ascii_field(value: str, length: int, pad: bytes = b" ") -> bytes:
    encoded = value.encode("ascii", errors="strict")
    if len(encoded) > length:
        encoded = encoded[:length]
    return encoded.ljust(length, pad)


def milliseconds_to_timecode(milliseconds: int, frame_rate: int, ceil: bool = False) -> tuple[int, int, int, int]:
    if frame_rate not in (25, 30):
        raise ValueError("EBU STL export supports 25 or 30 fps")
    frame_float = milliseconds * frame_rate / 1000
    total_frames = math.ceil(frame_float) if ceil else math.floor(frame_float)
    hours, remainder = divmod(total_frames, 3600 * frame_rate)
    minutes, remainder = divmod(remainder, 60 * frame_rate)
    seconds, frames = divmod(remainder, frame_rate)
    if hours > 23:
        raise ValueError("EBU STL timecode must be less than 24 hours")
    return hours, minutes, seconds, frames


def _frame_timecode(total_frames: int, frame_rate: int) -> bytes:
    if total_frames >= 24 * 60 * 60 * frame_rate:
        raise ValueError("EBU STL timecode must be less than 24 hours")
    hours, remainder = divmod(total_frames, 3600 * frame_rate)
    minutes, remainder = divmod(remainder, 60 * frame_rate)
    seconds, frames = divmod(remainder, frame_rate)
    return bytes((hours, minutes, seconds, frames))


def _ascii_timecode(milliseconds: int, frame_rate: int) -> str:
    return "".join(
        f"{value:02d}" for value in milliseconds_to_timecode(milliseconds, frame_rate)
    )


def _wrap_ebu_text(text: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        wrapped = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        )
        lines.extend(wrapped or [""])
    if len(lines) > max_lines:
        raise ValueError(
            f"caption requires {len(lines)} lines; EBU STL is configured for {max_lines}"
        )
    return lines


_ISO_6937_DIRECT = {
    "\n": b"\x8a",
    "\u00a0": b"\xa0",
    "\u00a1": b"\xa1",
    "\u00a2": b"\xa2",
    "\u00a3": b"\xa3",
    "\u00a5": b"\xa5",
    "\u00a7": b"\xa7",
    "\u00ab": b"\xab",
    "\u00b0": b"\xb0",
    "\u00b1": b"\xb1",
    "\u00b2": b"\xb2",
    "\u00b3": b"\xb3",
    "\u00b5": b"\xb5",
    "\u00b6": b"\xb6",
    "\u00b7": b"\xb7",
    "\u00bb": b"\xbb",
    "\u00bc": b"\xbc",
    "\u00bd": b"\xbd",
    "\u00be": b"\xbe",
    "\u00bf": b"\xbf",
    "\u00d7": b"\xb4",
    "\u00f7": b"\xb8",
    "\u2018": b"\xa9",
    "\u2019": b"\xb9",
    "\u201c": b"\xaa",
    "\u201d": b"\xba",
    "\u2190": b"\xac",
    "\u2191": b"\xad",
    "\u2192": b"\xae",
    "\u2193": b"\xaf",
    "\u2015": b"\xd0",
    "\u00b9": b"\xd1",
    "\u00ae": b"\xd2",
    "\u00a9": b"\xd3",
    "\u2122": b"\xd4",
    "\u266a": b"\xd5",
    "\u00ac": b"\xd6",
    "\u00a6": b"\xd7",
    "\u215b": b"\xdc",
    "\u215c": b"\xdd",
    "\u215d": b"\xde",
    "\u215e": b"\xdf",
    "\u2126": b"\xe0",
    "\u00c6": b"\xe1",
    "\u0110": b"\xe2",
    "\u00aa": b"\xe3",
    "\u0126": b"\xe4",
    "\u0132": b"\xe6",
    "\u013f": b"\xe7",
    "\u0141": b"\xe8",
    "\u00d8": b"\xe9",
    "\u0152": b"\xea",
    "\u00ba": b"\xeb",
    "\u00de": b"\xec",
    "\u0166": b"\xed",
    "\u014a": b"\xee",
    "\u0149": b"\xef",
    "\u0138": b"\xf0",
    "\u00e6": b"\xf1",
    "\u0111": b"\xf2",
    "\u00f0": b"\xf3",
    "\u0127": b"\xf4",
    "\u0131": b"\xf5",
    "\u0133": b"\xf6",
    "\u0140": b"\xf7",
    "\u0142": b"\xf8",
    "\u00f8": b"\xf9",
    "\u0153": b"\xfa",
    "\u00df": b"\xfb",
    "\u00fe": b"\xfc",
    "\u0167": b"\xfd",
    "\u014b": b"\xfe",
}

_ISO_6937_DIACRITICS = {
    "\u0300": 0xC1,
    "\u0301": 0xC2,
    "\u0302": 0xC3,
    "\u0303": 0xC4,
    "\u0304": 0xC5,
    "\u0306": 0xC6,
    "\u0307": 0xC7,
    "\u0308": 0xC8,
    "\u030a": 0xCA,
    "\u0327": 0xCB,
    "\u030b": 0xCD,
    "\u0328": 0xCE,
    "\u030c": 0xCF,
}

_ISO_6937_ALLOWED_BASES = {
    0xC1: set("AEIOUaeiou"),
    0xC2: set("ACEILNORSUYZaceilnorsuyz"),
    0xC3: set("ACEGHIJOSUWYaceghijosuwy"),
    0xC4: set("AINOUainou"),
    0xC5: set("AEIOUaeiou"),
    0xC6: set("AGUagu"),
    0xC7: set("CEGIZcegz"),
    0xC8: set("AEIOUYaeiouy"),
    0xCA: set("AUau"),
    0xCB: set("CGKLNRSTcklnrst"),
    0xCD: set("OUou"),
    0xCE: set("AEIUaeiu"),
    0xCF: set("CDELNRSTZcdelnrstz"),
}


def encode_iso_6937(text: str) -> bytes:
    output = bytearray()
    for index, character in enumerate(text):
        codepoint = ord(character)
        if 0x20 <= codepoint <= 0x7E:
            output.append(codepoint)
            continue
        direct = _ISO_6937_DIRECT.get(character)
        if direct is not None:
            output.extend(direct)
            continue
        if character == "\u0123":
            output.extend((0xC2, ord("g")))
            continue
        decomposed = unicodedata.normalize("NFD", character)
        if (
            len(decomposed) == 2
            and decomposed[0].isascii()
            and decomposed[0].isalpha()
            and decomposed[1] in _ISO_6937_DIACRITICS
        ):
            prefix = _ISO_6937_DIACRITICS[decomposed[1]]
            if decomposed[0] in _ISO_6937_ALLOWED_BASES[prefix]:
                output.extend((prefix, ord(decomposed[0])))
                continue
        raise UnicodeEncodeError(
            "iso-6937-2", text, index, index + 1,
            "character is not supported by the EBU STL Latin table",
        )
    return bytes(output)


def _text_blocks(cue: CaptionCue, width: int, max_lines: int) -> list[bytes]:
    lines = _wrap_ebu_text(cue.text, width, max_lines)
    encoded = encode_iso_6937("\n".join(lines))
    if not encoded:
        raise ValueError("EBU STL captions cannot be empty")
    terminated = encoded + b"\x8f"
    return [
        terminated[offset : offset + 112]
        for offset in range(0, len(terminated), 112)
    ]


def _gsi_header(
    project: CaptionProject,
    frame_rate: int,
    chars_per_line: int,
    total_blocks: int,
    creation_time: datetime,
) -> bytes:
    date = creation_time.astimezone(timezone.utc).strftime("%y%m%d")
    disk_format = {25: "STL25.01", 30: "STL30.01"}[frame_rate]
    language = project.language.lower().split("_", maxsplit=1)[0]
    try:
        language_code = _EBU_LANGUAGE_CODES[language]
    except KeyError as error:
        raise ValueError(
            "EBU STL export currently supports English (en), German (de), "
            "and Spanish (es) project metadata"
        ) from error
    first_in = project.cues[0].start_ms if project.cues else 0
    fields = [
        _ascii_field("850", 3),
        _ascii_field(disk_format, 8),
        _ascii_field("0", 1),
        _ascii_field("00", 2),
        _ascii_field(language_code, 2),
        _ascii_field("VIDEO SUBTITLE STUDIO", 32),
        _ascii_field("", 32),
        _ascii_field(project.source_filename, 32),
        _ascii_field("", 32),
        _ascii_field("", 32),
        _ascii_field("", 32),
        _ascii_field("VSS", 16),
        _ascii_field(date, 6),
        _ascii_field(date, 6),
        _ascii_field("00", 2),
        _ascii_field(str(total_blocks).zfill(5), 5),
        _ascii_field(str(len(project.cues)).zfill(5), 5),
        _ascii_field("001", 3),
        _ascii_field(str(chars_per_line).zfill(2), 2),
        _ascii_field("23", 2),
        _ascii_field("1", 1),
        _ascii_field("00000000", 8),
        _ascii_field(_ascii_timecode(first_in, frame_rate), 8),
        _ascii_field("1", 1),
        _ascii_field("1", 1),
        _ascii_field("GBR", 3),
        _ascii_field("", 32),
        _ascii_field("", 32),
        _ascii_field("", 32),
        bytes([0x20]) * 75,
        bytes(576),
    ]
    header = b"".join(fields)
    if len(header) != 1024:
        raise AssertionError(f"invalid GSI header length: {len(header)}")
    return header


def export_ebu_stl(
    project: CaptionProject,
    frame_rate: int = 25,
    chars_per_line: int = 40,
    max_lines: int = 2,
    creation_time: datetime | None = None,
) -> bytes:
    if frame_rate not in (25, 30):
        raise ValueError("EBU STL export supports 25 or 30 fps")
    prepared: list[tuple[CaptionCue, list[bytes], int, int]] = []
    previous_out_frame = 0
    for cue in project.cues:
        start_frame = math.floor(cue.start_ms * frame_rate / 1000)
        out_frame = math.ceil(cue.end_ms * frame_rate / 1000)
        start_frame = max(start_frame, previous_out_frame)
        if out_frame <= start_frame:
            raise ValueError(
                f"caption {cue.id!r} is too short or too close to the previous "
                f"caption at {frame_rate} fps"
            )
        _frame_timecode(start_frame, frame_rate)
        _frame_timecode(out_frame, frame_rate)
        prepared.append(
            (
                cue,
                _text_blocks(cue, chars_per_line, max_lines),
                start_frame,
                out_frame,
            )
        )
        previous_out_frame = out_frame
    total_blocks = sum(len(blocks) for _, blocks, _, _ in prepared)
    if total_blocks > 99999 or len(project.cues) > 99999:
        raise ValueError("project exceeds EBU STL block limits")

    output = bytearray(
        _gsi_header(
            project,
            frame_rate,
            chars_per_line,
            total_blocks,
            creation_time or datetime.now(timezone.utc),
        )
    )
    for subtitle_number, (cue, text_blocks, start_frame, out_frame) in enumerate(
        prepared, start=1
    ):
        for extension_number, text_block in enumerate(text_blocks):
            extension = 0xFF if extension_number == len(text_blocks) - 1 else extension_number
            vertical_position = max(1, 23 - len(_wrap_ebu_text(cue.text, chars_per_line, max_lines)))
            output.extend(
                struct.pack("<BHB", 0, subtitle_number, extension)
                + bytes([0])
                + _frame_timecode(start_frame, frame_rate)
                + _frame_timecode(out_frame, frame_rate)
                + bytes([vertical_position, 2, 0])
                + text_block.ljust(112, b"\x8f")
            )
    return bytes(output)

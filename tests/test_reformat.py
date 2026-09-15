import pytest

from subtitle_studio.models import CaptionCue, ReformatRequest, ReformatSettings, VideoStyle, WordCasing
from subtitle_studio.processing import (
    REFORMAT_PRESETS,
    estimate_word_timings,
    reformat_cues,
    resolve_reformat_settings,
)


def test_estimate_word_timings_splits_proportionally_to_word_length() -> None:
    timings = estimate_word_timings("hi there world", 0, 900)
    assert [word for word, _, _ in timings] == ["hi", "there", "world"]
    # Weighted by character length (2, 5, 5 -> total 12), longer words get more time.
    assert timings[0][2] - timings[0][1] < timings[1][2] - timings[1][1]
    assert timings[-1][2] == 900


def test_estimate_word_timings_handles_empty_text() -> None:
    assert estimate_word_timings("", 0, 1000) == []


def test_estimate_word_timings_guarantees_monotonic_non_overlapping_words() -> None:
    timings = estimate_word_timings("a b c d e f g h", 0, 4)
    for index in range(1, len(timings)):
        assert timings[index][1] >= timings[index - 1][2]


def test_reformat_cues_splits_into_word_count_blocks() -> None:
    cues = [CaptionCue(start_ms=0, end_ms=4000, text="the quick brown fox jumps")]
    settings = ReformatSettings(words_per_block=2)

    result = reformat_cues(cues, settings)

    assert [cue.text for cue in result] == ["the quick", "brown fox", "jumps"]
    assert result[0].start_ms == 0
    assert result[-1].end_ms == 4000
    # Blocks stay ordered and non-overlapping.
    for index in range(1, len(result)):
        assert result[index].start_ms >= result[index - 1].end_ms


def test_reformat_cues_keeps_whole_cue_when_no_word_limit() -> None:
    cues = [CaptionCue(start_ms=0, end_ms=2000, text="hello, world!")]
    settings = ReformatSettings(words_per_block=None)

    result = reformat_cues(cues, settings)

    assert len(result) == 1
    assert result[0].text == "hello, world!"


def test_reformat_cues_removes_punctuation() -> None:
    cues = [CaptionCue(start_ms=0, end_ms=2000, text="Well, \"hello\" world, now!")]
    settings = ReformatSettings(words_per_block=None, remove_punctuation=True)

    result = reformat_cues(cues, settings)

    assert result[0].text == "Well hello world now"


def test_reformat_cues_applies_casing() -> None:
    cues = [CaptionCue(start_ms=0, end_ms=2000, text="Shout This")]

    upper = reformat_cues(cues, ReformatSettings(casing=WordCasing.UPPER))
    lower = reformat_cues(cues, ReformatSettings(casing=WordCasing.LOWER))

    assert upper[0].text == "SHOUT THIS"
    assert lower[0].text == "shout this"


def test_reformat_cues_does_not_merge_across_source_cues() -> None:
    cues = [
        CaptionCue(start_ms=0, end_ms=1000, text="one"),
        CaptionCue(start_ms=2000, end_ms=3000, text="two three"),
    ]
    settings = ReformatSettings(words_per_block=5)

    result = reformat_cues(cues, settings)

    assert [cue.text for cue in result] == ["one", "two three"]


@pytest.mark.parametrize(
    ("style", "expected_words_per_block", "expected_remove_punctuation"),
    [
        (VideoStyle.LANDSCAPE, None, False),
        (VideoStyle.SQUARE, 5, False),
        (VideoStyle.VERTICAL, 2, True),
    ],
)
def test_presets_have_sensible_defaults(
    style: VideoStyle, expected_words_per_block: int | None, expected_remove_punctuation: bool
) -> None:
    preset = REFORMAT_PRESETS[style]
    assert preset.words_per_block == expected_words_per_block
    assert preset.remove_punctuation is expected_remove_punctuation


def test_resolve_reformat_settings_merges_overrides_onto_preset() -> None:
    request = ReformatRequest(style=VideoStyle.VERTICAL, words_per_block=1)

    resolved = resolve_reformat_settings(request)

    assert resolved.words_per_block == 1
    assert resolved.remove_punctuation is True


def test_resolve_reformat_settings_without_style_defaults_to_no_op() -> None:
    resolved = resolve_reformat_settings(ReformatRequest())

    assert resolved.words_per_block is None
    assert resolved.remove_punctuation is False
    assert resolved.casing == WordCasing.DEFAULT

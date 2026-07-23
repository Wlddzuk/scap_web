"""Focused tests for phrase-safe, karaoke-ready caption grouping."""

import re

import video_generator


def timed_words(texts):
    return [
        {"text": text, "start": index * 0.3, "end": (index + 1) * 0.3}
        for index, text in enumerate(texts)
    ]


def test_number_stays_with_unit_and_preposition_does_not_end_cue(monkeypatch):
    monkeypatch.setenv("CAPTION_UPPERCASE", "true")
    groups = video_generator.group_words_for_captions(
        timed_words(
            ["the", "universe", "from", "11", "billion", "years", "ago"]
        )
    )

    assert all(
        group["text"].split()[-1].lower()
        not in video_generator._CAPTION_WEAK_END_WORDS
        for group in groups[:-1]
    )
    for left, right in zip(groups, groups[1:]):
        left_token = left["text"].split()[-1]
        right_token = right["text"].split()[0].lower()
        assert not (
            video_generator._caption_token_is_number(left_token)
            and right_token in video_generator._CAPTION_NUMBER_UNITS
        )
    assert any("11 BILLION YEARS" in group["text"] for group in groups)


def test_compound_light_year_unit_stays_with_number(monkeypatch):
    monkeypatch.setenv("CAPTION_UPPERCASE", "true")

    groups = video_generator.group_words_for_captions(
        timed_words(
            ["signal", "crossed", "3.5", "billion", "light", "years", "away"]
        )
    )

    assert any("3.5 BILLION LIGHT YEARS" in group["text"] for group in groups)


def test_phrase_rules_survive_a_hard_two_word_target(monkeypatch):
    monkeypatch.setenv("CAPTION_UPPERCASE", "true")
    groups = video_generator.group_words_for_captions(
        timed_words(["we", "looked", "at", "42", "million", "stars"]),
        min_words=2,
        max_words=2,
    )

    assert all(not group["text"].endswith(" AT") for group in groups[:-1])
    assert any("42 MILLION" in group["text"] for group in groups)


def test_casing_and_trailing_punctuation_are_consistent(monkeypatch):
    words = timed_words(["Hello,", "world!"])

    monkeypatch.setenv("CAPTION_UPPERCASE", "false")
    mixed_case = video_generator.group_words_for_captions(words)
    assert mixed_case[0]["text"] == "Hello, world"
    assert mixed_case[0]["words"][-1]["text"] == "world"

    monkeypatch.setenv("CAPTION_UPPERCASE", "true")
    uppercase = video_generator.group_words_for_captions(words)
    assert uppercase[0]["text"] == "HELLO, WORLD"
    assert not re.search(r"[,.!?;:]$", uppercase[0]["text"])


def test_groups_preserve_word_timings_for_karaoke(monkeypatch):
    monkeypatch.setenv("CAPTION_UPPERCASE", "true")
    words = timed_words(["new", "evidence", "changes", "everything"])

    groups = video_generator.group_words_for_captions(words)
    flattened = [word for group in groups for word in group["words"]]

    assert [word["text"] for word in flattened] == [
        "NEW", "EVIDENCE", "CHANGES", "EVERYTHING"
    ]
    assert [word["start"] for word in flattened] == [word["start"] for word in words]
    assert [word["end"] for word in flattened] == [word["end"] for word in words]

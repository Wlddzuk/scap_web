"""Regressions from article 82 ("Ancient meteorites…", 2026-09-26).

That render captioned "calcium-aluminum-rich" as "-RICH" and "microteslas" as
"MICRO -TESTLESS", and its solid top/bottom grade bands read as letterboxing.
"""

from PIL import Image

import video_generator as vg


def _timed(text):
    return [
        {"text": token, "start": i * 0.3, "end": i * 0.3 + 0.25}
        for i, token in enumerate(text.split())
    ]


def test_captions_take_spelling_from_the_script():
    heard = _timed(
        "These calcium -aluminum -rich inclusions, formed in the first 200 ,000 "
        "years. The field was 150 to 600 micro -testless."
    )
    script = (
        "These calcium-aluminum-rich inclusions, formed in the first 200,000 "
        "years. The field was 150 to 600 microteslas."
    )
    words = vg.align_words_to_script(heard, script)
    texts = [w["text"] for w in words]
    assert "calcium-aluminum-rich" in texts
    assert "200,000" in texts
    assert "microteslas." in texts
    assert not any(t.startswith(("-", ",")) for t in texts)
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)


def test_large_divergence_keeps_whisper_words():
    heard = _timed("completely different words were spoken here today friends")
    words = vg.align_words_to_script(heard, "The narration said something else entirely.")
    assert [w["text"] for w in words] == [w["text"] for w in heard]


def test_shot_grade_has_no_hard_band_edges():
    flat = Image.new("RGB", (1080, 1920), (180, 180, 180))
    graded = vg.shot_variant(flat, 0, ((1.0, (0.5, 0.5)),))
    column = [graded.getpixel((540, y))[0] for y in range(1920)]
    steps = [abs(a - b) for a, b in zip(column, column[1:])]
    assert max(steps) <= 2  # the old solid rectangles jumped ~30 levels in one row
    assert column[0] < column[900] and column[1919] < column[900]

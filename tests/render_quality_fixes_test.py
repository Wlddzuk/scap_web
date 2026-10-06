"""Regressions from article 82 ("Ancient meteorites…", 2026-09-26).

That render showed one blurred image for ~25 of 50 seconds, framed most
archive photos as a box over black-looking bars, and captioned
"calcium-aluminum-rich" as "-RICH" and "microteslas" as "MICRO -TESTLESS".
"""

from types import SimpleNamespace

from PIL import Image, ImageDraw

import real_imagery
import video_generator as vg


def _ink_on_paper():
    """The default Illustrated Science look: cobalt strokes on off-white."""
    image = Image.new("RGB", (1080, 1920), (244, 240, 230))
    draw = ImageDraw.Draw(image)
    for x in range(60, 1080, 90):
        draw.rectangle((x, 300, x + 30, 1600), fill=(20, 93, 160))
    return image


def _scene(index, query):
    return {
        "speech": f"Scene {index} explains {query}.",
        "visual": query,
        "referent": "object",
        "referent_query": query,
        "evidence_query": query,
        "precise_claim": False,
        "graphic_payload": "",
        "_scene_index": index,
    }


def test_generated_paper_illustrations_pass_the_gate_but_scans_do_not():
    illustration = _ink_on_paper()
    assert vg._documentary_image_is_usable(illustration) is False
    assert vg._documentary_image_is_usable(illustration, generated=True) is True
    blank = Image.new("RGB", (1080, 1920), (120, 120, 120))
    assert vg._documentary_image_is_usable(blank, generated=True) is False


def _patch_sources(monkeypatch, found_queries, generated):
    colours = {}

    def fetch(query, **_kwargs):
        key = next((q for q in found_queries if q in str(query)), None)
        if key is None:
            return None
        colour = colours.setdefault(key, (40 + 60 * len(colours), 90, 160))
        image = Image.new("RGB", (1080, 1920), colour)
        ImageDraw.Draw(image).ellipse((200, 400, 880, 1100), fill="gold")
        url = f"https://example.test/{key.replace(' ', '_')}.jpg"
        return SimpleNamespace(
            image=image,
            source_url=url,
            audit_record=lambda lane: {
                "lane": lane, "provider": "Wikimedia", "source_url": url,
            },
        )

    monkeypatch.setattr(real_imagery, "fetch_referent_image", fetch)
    monkeypatch.setattr(vg, "search_pexels_images", lambda *_a, **_k: [])
    monkeypatch.setattr(vg, "_credit_photo", lambda image, _source: image)
    monkeypatch.setattr(
        vg, "_parallel_image_gen", lambda prompts, **_k: [generated() for _ in prompts]
    )


def test_generated_illustrations_fill_missing_scenes(monkeypatch):
    _patch_sources(monkeypatch, ["meteorite"], generated=_ink_on_paper)
    records = []
    vg.generate_referent_scene_images(
        [_scene(0, "meteorite"), _scene(1, "solar nebula"), _scene(2, "magnetic field")],
        article_title="Ancient meteorites",
        visual_sources_out=records,
    )
    assert [r["provider"] for r in records] == ["Wikimedia", "FAL", "FAL"]
    assert not any(r.get("editorial_reuse") for r in records)


def test_fallback_reuse_never_repeats_one_image_back_to_back(monkeypatch):
    _patch_sources(monkeypatch, ["antarctica", "meteorite"], generated=lambda: None)
    records = []
    shots = [
        _scene(0, "meteorite"),
        _scene(1, "antarctica"),
        _scene(2, "meteorite grains"),
        _scene(3, "meteorite field"),
        _scene(4, "meteorite dust"),
    ]
    vg.generate_referent_scene_images(
        shots, article_title="Ancient meteorites", visual_sources_out=records
    )
    sources = [r["source_url"] for r in records]
    assert all(a != b for a, b in zip(sources, sources[1:])), sources
    assert len(set(sources)) == 2


def test_wide_archive_photo_fills_the_frame_without_dark_bands():
    photo = Image.new("RGB", (1800, 1013), (200, 120, 60))
    ImageDraw.Draw(photo).rectangle((700, 300, 1100, 700), fill=(30, 30, 30))
    frame = vg._documentary_frame_image(photo, 1080, 1920)
    assert frame.size == (1080, 1920)
    # Top and bottom edges show the photo itself, not a darkened blur.
    for y in (5, 1914):
        r, g, b = frame.getpixel((540 if y == 5 else 20, y))
        assert r > 170 and g > 100, (y, (r, g, b))


def test_tiny_wide_photo_keeps_the_blurred_surround():
    photo = Image.new("RGB", (600, 338), (200, 120, 60))
    frame = vg._documentary_frame_image(photo, 1080, 1920)
    assert frame.getpixel((540, 5))[0] < 170  # darkened surround, not upscaled 5x


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


def test_photo_grade_has_no_hard_band_edges():
    flat = Image.new("RGB", (1080, 1920), (180, 180, 180))
    graded = vg._documentary_photo_variant(flat, {}, 0)
    column = [graded.getpixel((540, y))[0] for y in range(1920)]
    steps = [abs(a - b) for a, b in zip(column, column[1:])]
    assert max(steps) <= 2  # the old solid rectangles jumped ~30 levels in one row
    assert column[0] < column[900] and column[1919] < column[900]

"""Safety and routing tests for referent-based visual sourcing."""

import json
from types import SimpleNamespace

from PIL import Image, ImageChops, ImageDraw

import real_imagery
import summarizer
import video_generator
from visual_router import GRAPHIC, PHOTO, SCHEMATIC, route_scene


def _scene(**overrides):
    scene = {
        "speech": "The result changed after one day.",
        "visual": "a simple explanation",
        "visual_action": "highlight",
        "referent": "abstract",
        "referent_query": "",
        "precise_claim": False,
        "graphic_payload": "38% after 24h",
    }
    scene.update(overrides)
    return scene


def test_route_scene_five_rules():
    assert route_scene(_scene(referent="abstract")) == GRAPHIC
    assert route_scene(_scene(referent="object", referent_query="Milgram apparatus")) == PHOTO
    assert route_scene(_scene(referent="unphotographable", precise_claim=True)) == GRAPHIC
    assert route_scene(_scene(referent="unphotographable", precise_claim=False)) == SCHEMATIC
    assert route_scene(_scene(referent="object", referent_query="")) == GRAPHIC


def test_precise_unphotographable_claim_never_generates():
    lane = route_scene(_scene(referent="unphotographable", precise_claim=True))
    assert lane == GRAPHIC
    assert lane != SCHEMATIC


def test_parse_response_normalizes_unknown_referent_safely():
    payload = {
        "tldr": "A summary.",
        "bullets": ["A fact."],
        "video_script": "A sentence.",
        "hashtags": ["#One", "#Two", "#Three"],
        "hook_variants": ["A sentence.", "A second hook.", "A third hook."],
        "best_hook_index": 0,
        "dominant_emotion": "curious",
        "cover_line": "A SCIENCE FACT",
        "cta_question": "A sentence.",
        "search_caption": "A science fact.",
        "series_lane": "other",
        "scenes": [_scene(referent="hallucinated_lane", referent_query="wrong query", precise_claim="maybe")],
    }
    parsed = summarizer.parse_response(json.dumps(payload))
    assert parsed["scenes"][0]["referent"] == "abstract"
    assert parsed["scenes"][0]["referent_query"] == ""
    assert parsed["scenes"][0]["precise_claim"] is True


def test_stock_is_used_only_when_the_story_subject_is_confirmed(monkeypatch):
    monkeypatch.setattr(real_imagery, "fetch_referent_image", lambda *_args, **_kwargs: None)
    stock = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(stock).ellipse((220, 420, 860, 1060), fill="gold")
    monkeypatch.setattr(
        video_generator,
        "search_pexels_images",
        lambda *_args, **_kwargs: [stock],
    )
    monkeypatch.setattr(real_imagery, "_verify_subject", lambda *_args, **_kwargs: True)
    records = []
    images = video_generator.generate_referent_scene_images(
        [{**_scene(referent="object", referent_query="Milgram apparatus"), "_scene_index": 0}],
        visual_sources_out=records,
    )
    assert images[0].size == (1080, 1920)
    assert records[0]["lane"] == PHOTO
    assert records[0]["provider"] == "Pexels"
    assert records[0].get("editorial_reuse") is not True
    assert records[0]["subject_verified"] is True


def test_unconfirmed_stock_is_generated_over_rather_than_used(monkeypatch):
    """Stock that cannot be confirmed against the story must not be used.

    Accepting it is exactly how a search for "Individual bundle" put a photo of
    plastic bags into a scene about cells bundling together. A generated frame
    prompted with the real story subject is the better failure mode.
    """
    monkeypatch.setattr(real_imagery, "fetch_referent_image", lambda *_args, **_kwargs: None)
    stock = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(stock).ellipse((220, 420, 860, 1060), fill="gold")
    monkeypatch.setattr(
        video_generator,
        "search_pexels_images",
        lambda *_args, **_kwargs: [stock],
    )
    monkeypatch.setattr(real_imagery, "_verify_subject", lambda *_args, **_kwargs: False)
    generated_image = Image.new("RGB", (1080, 1920), "maroon")
    ImageDraw.Draw(generated_image).rectangle((240, 380, 840, 1120), fill="gold")
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: [generated_image.copy() for _prompt in prompts],
    )
    records = []
    video_generator.generate_referent_scene_images(
        [{**_scene(referent="object", referent_query="Milgram apparatus"), "_scene_index": 0}],
        visual_sources_out=records,
    )
    assert records[0]["provider"] == "FAL"


def test_documentary_edit_reuses_hero_with_distinct_crops(monkeypatch):
    hero = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(hero).ellipse((280, 420, 800, 940), fill="gold")
    monkeypatch.setattr(real_imagery, "fetch_hero_image", lambda *_args, **_kwargs: hero)
    monkeypatch.setattr(real_imagery, "fetch_referent_image", lambda *_args, **_kwargs: None)
    generated = Image.new("RGB", (1080, 1920), "maroon")
    ImageDraw.Draw(generated).rectangle((240, 380, 840, 1120), fill="gold")
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: [generated.copy() for _prompt in prompts],
    )
    records = []
    shots = [
        {**_scene(), "_scene_index": 0, "_shot_step": 0, "_shot_type": "wide establishing shot"},
        {**_scene(), "_scene_index": 0, "_shot_step": 1, "_shot_type": "macro close-up"},
        {**_scene(), "_scene_index": 1, "_shot_step": 0, "_shot_type": "close-up detail"},
    ]
    images = video_generator.generate_referent_scene_images(
        shots,
        article_title="A real science story",
        hero_image="https://example.test/hero.jpg",
        visual_sources_out=records,
    )
    assert len(images) == 3
    assert all(image.size == (1080, 1920) for image in images)
    assert records[0]["provider"] == "Source article"
    assert records[0]["lane"] == PHOTO
    # Shots 0 and 1 share _scene_index 0, so the hero still carries both via
    # distinct crops. Scene 1 has no photograph of its own and is now generated
    # rather than served another crop of an unrelated hero -- recycling one photo
    # across every scene is what made earlier renders repetitive.
    assert records[1]["lane"] == GRAPHIC
    assert records[1]["provider"] == "FAL"


def test_symbolic_prompt_requires_story_subject_and_rejects_empty_geometry():
    prompt = video_generator._symbolic_prompt(
        _scene(
            speech="Both animals glow at the edge under moonlight.",
            visual="an ibis and a baboon outlined by moonlight",
        ),
        "vivid",
        "Why Thoth appeared as an ibis and a baboon",
    )

    assert "Why Thoth appeared as an ibis and a baboon" in prompt
    assert "an ibis and a baboon outlined by moonlight" in prompt
    assert "Never substitute an unrelated animal, deity" in prompt
    assert "one lone abstract shape" in prompt
    assert "No technical diagram" in prompt


def test_documentary_queries_split_combined_subjects_around_named_entity():
    scene = {
        "speech": "Ancient artists depicted Thoth as a bird or baboon.",
        "referent_query": "ancient Egyptian ibis baboon artwork",
    }
    variants = video_generator._documentary_query_variants(
        scene,
        "Moonlight may explain Egyptian god Thoth's forms",
    )
    assert variants[0] == "ancient Egyptian ibis baboon artwork"
    assert variants[1:3] == ["Thoth ibis", "Thoth baboon"]


def test_atmospheric_referents_prefer_real_stock_over_catalogue_metadata():
    assert video_generator._documentary_prefers_stock(
        "ancient Egyptian desert night moon"
    ) is True
    assert video_generator._documentary_prefers_stock("full moon Egyptian sky") is True
    assert video_generator._documentary_prefers_stock(
        "ancient Egyptian papyrus moon"
    ) is False
    assert video_generator._documentary_prefers_stock("Thoth ibis sculpture") is False


def test_documentary_split_photo_uses_two_real_frames():
    left = Image.new("RGB", (1080, 1920), "navy")
    right = Image.new("RGB", (1080, 1920), "maroon")
    result = video_generator._documentary_split_photo(left, right)
    assert result.size == (1080, 1920)
    assert result.getpixel((100, 900)) == (0, 0, 128)
    assert result.getpixel((980, 900)) == (128, 0, 0)
    assert result.getpixel((540, 900)) == (255, 205, 54)


def test_story_reference_assets_prefer_repeated_named_subjects():
    image = Image.new("RGB", (1080, 1920), "navy")
    pool = [
        (image, {"provider": "Wikimedia Commons", "source_url": "papyrus", "search_query": "Thoth papyrus"}),
        (image, {"provider": "Wikimedia Commons", "source_url": "ibis", "search_query": "Thoth ibis"}),
        (image, {"provider": "Wikimedia Commons", "source_url": "baboon", "search_query": "Thoth baboon"}),
        (image, {"provider": "Wikimedia Commons", "source_url": "wall", "search_query": "Egyptian temple wall"}),
    ]
    scenes = [
        _scene(speech="The ibis and baboon were Thoth's two forms."),
        _scene(speech="The bird's feathers and the primate's fur reflected moonlight."),
        _scene(speech="Which animal form fits better?"),
    ]

    references = video_generator._story_reference_assets(scenes, pool)

    assert [record["source_url"] for _image, record in references] == [
        "ibis",
        "baboon",
    ]


def test_reference_backed_edit_never_invents_a_replacement_subject():
    ibis = Image.new("RGB", (1080, 1920), "navy")
    baboon = Image.new("RGB", (1080, 1920), "maroon")
    references = [
        (ibis, {"provider": "Museum", "source_url": "ibis"}),
        (baboon, {"provider": "Museum", "source_url": "baboon"}),
    ]

    image, record = video_generator._reference_backed_scene_asset(references, 0)

    assert image.getpixel((100, 900)) == (0, 0, 128)
    assert image.getpixel((980, 900)) == (128, 0, 0)
    assert record["secondary_source_url"] == "baboon"
    assert record["edit"] == "reference-backed split comparison"


def test_evidence_query_prefers_the_finding_over_the_location():
    scene = _scene(
        speech="Researchers in Jerusalem found double-strand breaks in tumor DNA.",
        visual="a DNA helix breaking inside a highlighted super-enhancer region",
        referent="object",
        referent_query="Hebrew University Jerusalem",
        visual_role="discovery",
        evidence_query="tumor DNA double-strand breaks",
        focus_label="DOUBLE-STRAND BREAKS",
    )

    assert video_generator._scene_evidence_query(
        scene,
        "Cancer may be breaking its own DNA",
    ) == "tumor DNA double-strand breaks"


def test_old_location_scene_is_inferred_as_context_only():
    assert video_generator._scene_visual_role({
        "referent_query": "Hebrew University Jerusalem campus building",
    }) == "context"


def test_non_comparison_discovery_generates_instead_of_reusing_location(monkeypatch):
    location = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(location).rectangle((180, 300, 900, 1300), fill="gold")
    generated = Image.new("RGB", (1080, 1920), "maroon")
    ImageDraw.Draw(generated).ellipse((220, 420, 860, 1060), fill="gold")

    def fake_fetch(query, **_kwargs):
        if "Jerusalem" not in query:
            return None
        return SimpleNamespace(
            image=location,
            source_url="https://example.test/jerusalem.jpg",
            audit_record=lambda lane: {
                "lane": lane,
                "provider": "Archive",
                "source_url": "https://example.test/jerusalem.jpg",
                "search_query": query,
            },
            license="Public domain",
            author="Archive",
            source_name="Archive",
        )

    monkeypatch.setattr(real_imagery, "fetch_referent_image", fake_fetch)
    monkeypatch.setattr(video_generator, "_credit_photo", lambda image, _source: image)
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: [generated.copy() for _prompt in prompts],
    )

    records = []
    images = video_generator.generate_referent_scene_images(
        [
            {
                **_scene(
                    referent="object",
                    referent_query="Hebrew University Jerusalem",
                    visual_role="context",
                    evidence_query="Hebrew University Jerusalem",
                ),
                "_scene_index": 0,
            },
            {
                **_scene(
                    speech="The team found double-strand breaks in tumor DNA.",
                    visual="a DNA helix with a clean double-strand break",
                    visual_role="discovery",
                    evidence_query="tumor DNA double-strand breaks",
                    precise_claim=True,
                ),
                "_scene_index": 1,
            },
        ],
        article_title="Cancer DNA discovery",
        visual_sources_out=records,
    )

    assert len(images) == 2
    assert records[0]["source_url"].endswith("jerusalem.jpg")
    assert records[1]["provider"] == "FAL"
    assert records[1]["evidence_query"] == "tumor DNA double-strand breaks"
    assert records[1].get("editorial_reuse") is not True


def test_explicit_matching_comparison_can_reuse_two_verified_subjects(monkeypatch):
    ibis = Image.new("RGB", (1080, 1920), "navy")
    baboon = Image.new("RGB", (1080, 1920), "maroon")
    ImageDraw.Draw(ibis).ellipse((220, 420, 860, 1060), fill="gold")
    ImageDraw.Draw(baboon).rectangle((220, 420, 860, 1060), fill="gold")
    generated_calls = []

    sources = {
        "Thoth ibis": SimpleNamespace(
            image=ibis,
            source_url="https://example.test/ibis.jpg",
            audit_record=lambda lane: {
                "lane": lane,
                "provider": "Museum",
                "source_url": "https://example.test/ibis.jpg",
            },
            license="Public domain",
            author="Museum",
            source_name="Museum",
        ),
        "Thoth baboon": SimpleNamespace(
            image=baboon,
            source_url="https://example.test/baboon.jpg",
            audit_record=lambda lane: {
                "lane": lane,
                "provider": "Museum",
                "source_url": "https://example.test/baboon.jpg",
            },
            license="Public domain",
            author="Museum",
            source_name="Museum",
        ),
    }
    monkeypatch.setattr(
        real_imagery,
        "fetch_referent_image",
        lambda query, **_kwargs: sources.get(query),
    )
    monkeypatch.setattr(video_generator, "_credit_photo", lambda image, _source: image)
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: generated_calls.extend(prompts) or [],
    )

    records = []
    video_generator.generate_referent_scene_images(
        [
            {**_scene(referent="object", referent_query="Thoth ibis", evidence_query="Thoth ibis"), "_scene_index": 0},
            {**_scene(referent="object", referent_query="Thoth baboon", evidence_query="Thoth baboon"), "_scene_index": 1},
            {
                **_scene(
                    speech="Which Thoth animal, ibis or baboon, fits better?",
                    visual="the Thoth ibis beside the Thoth baboon",
                    evidence_query="Thoth ibis baboon comparison",
                ),
                "_scene_index": 2,
            },
        ],
        article_title="Thoth ibis and baboon",
        visual_sources_out=records,
    )

    assert records[2]["edit"] == "reference-backed split comparison"
    assert generated_calls == []


def test_contextual_comparison_keeps_real_context_and_both_references():
    context = Image.new("RGB", (1080, 1920), "darkblue")
    ibis = Image.new("RGB", (1080, 1920), "navy")
    baboon = Image.new("RGB", (1080, 1920), "maroon")
    result, record = video_generator._contextual_reference_comparison(
        context,
        [
            (ibis, {"provider": "Museum", "source_url": "ibis"}),
            (baboon, {"provider": "Museum", "source_url": "baboon"}),
        ],
    )

    assert result.size == (1080, 1920)
    assert result.getpixel((120, 900)) == (0, 0, 128)
    assert result.getpixel((930, 900)) == (128, 0, 0)
    assert result.getpixel((20, 100)) != result.getpixel((120, 900))
    assert record["edit"] == "contextual reference-backed comparison"


def test_ken_burns_pan_changes_framing_without_changing_frame_size():
    source = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(source).rectangle((0, 0, 180, 1919), fill="gold")
    clip = video_generator.create_clip(
        source,
        duration=2.0,
        zoom_factor=0.08,
        motion="pan-left",
    )
    try:
        first = Image.fromarray(clip.get_frame(0.0))
        last = Image.fromarray(clip.get_frame(1.99))
        assert first.size == (1080, 1920)
        assert last.size == (1080, 1920)
        assert ImageChops.difference(first, last).getbbox() is not None
    finally:
        clip.close()


def test_documentary_frame_keeps_an_off_centre_archive_subject_visible():
    source = Image.new("RGB", (1600, 900), "white")
    draw = ImageDraw.Draw(source)
    draw.rectangle((1450, 180, 1599, 760), fill="black")

    framed = video_generator._documentary_frame_image(source, 1080, 1920)

    assert framed.size == (1080, 1920)
    # The full source is fitted over the background, so the edge-positioned
    # evidence survives instead of being removed by a centred 9:16 crop.
    assert sum(1 for pixel in framed.getdata() if max(pixel) < 40) > 15_000


def test_documentary_quality_gate_rejects_empty_frames_and_scanned_diagrams():
    blank = Image.new("RGB", (1080, 1920), (120, 120, 120))

    # A photograph carries most of its pixels in the mid range. Measured real
    # sources sit at mid 0.54 (snowy landscape) to 1.00 (stained micrograph).
    detailed = Image.new("RGB", (1080, 1920))
    detailed.putdata([
        (90 + (x * 7 + y * 13) % 110, 80 + (x * 3 + y * 11) % 120, 70 + (x + y) % 130)
        for y in range(1920)
        for x in range(1080)
    ])

    # Ink on a white page: a large near-white ground and almost no mid-tone.
    # This is the signature of the scanned NASA schematic (white 0.59, mid 0.11)
    # that reached a biology video and was unreadable at phone size.
    scanned = Image.new("RGB", (1080, 1920), "white")
    draw = ImageDraw.Draw(scanned)
    for x in range(0, 1080, 80):
        draw.rectangle((x, 0, x + 39, 1919), fill="black")

    assert video_generator._documentary_image_is_usable(blank) is False
    assert video_generator._documentary_image_is_usable(detailed) is True
    assert video_generator._documentary_image_is_usable(scanned) is False


def test_documentary_hook_cuts_between_distinct_real_images(monkeypatch):
    used_colours = []
    generated = []

    monkeypatch.setattr(
        video_generator,
        "create_clip",
        lambda image, duration, **_kwargs: (
            used_colours.append(image.getpixel((0, 0)))
            or SimpleNamespace(duration=duration)
        ),
    )
    monkeypatch.setattr(
        video_generator,
        "generate_image_fal",
        lambda *_args, **_kwargs: generated.append(True),
    )

    clips = video_generator.create_hook_clips(
        "A real science story",
        duration=4.0,
        opening_images=[
            Image.new("RGB", (20, 20), colour)
            for colour in ("navy", "maroon", "gold", "teal")
        ],
    )

    assert len(clips) == 4
    assert len(set(used_colours)) == 4
    assert generated == []


def test_non_public_domain_candidate_requires_author():
    missing = real_imagery.ImageCandidate("https://example.com/a.jpg", "Commons", "https://example.com/page", "CC BY-SA", "")
    complete = real_imagery.ImageCandidate("https://example.com/a.jpg", "Commons", "https://example.com/page", "CC BY-SA", "Jane Doe")
    public = real_imagery.ImageCandidate("https://example.com/a.jpg", "NASA", "https://example.com/page", "Public domain", "")
    noncommercial = real_imagery.ImageCandidate("https://example.com/a.jpg", "Commons", "https://example.com/page", "CC BY-NC-SA", "Jane Doe")
    assert real_imagery.candidate_has_required_attribution(missing) is False
    assert real_imagery.candidate_has_required_attribution(complete) is True
    assert real_imagery.candidate_has_required_attribution(public) is True
    assert real_imagery.candidate_has_required_attribution(noncommercial) is False


def test_provider_metadata_can_verify_subject_when_vision_is_unavailable():
    candidate = real_imagery.ImageCandidate(
        "https://example.com/a.jpg",
        "NASA",
        "https://example.com/page",
        "Public domain",
        "",
        "Curiosity rover panorama showing polygonal ground on Mars",
    )
    assert real_imagery.metadata_subject_matches(
        candidate,
        "Mars polygonal ground landscape",
    ) is True
    assert real_imagery.metadata_subject_matches(
        candidate,
        "Perseid meteor shower",
    ) is False


def test_authoritative_catalogue_can_override_bad_vision_but_not_pdf():
    museum = real_imagery.ImageCandidate(
        "https://upload.wikimedia.org/thoth.jpg",
        "Wikimedia Commons",
        "https://commons.wikimedia.org/wiki/File:Thoth-Ibis.jpg",
        "Public domain",
        "Museum",
        "Egyptian Thoth-Ibis figure from the Walters Art Museum",
    )
    pdf = real_imagery.ImageCandidate(
        "https://upload.wikimedia.org/book.jpg",
        "Wikimedia Commons",
        "https://commons.wikimedia.org/wiki/File:Ancient_Egypt.pdf",
        "Public domain",
        "Archive",
        "Ancient Egyptian papyrus moon catalogue.pdf",
    )
    assert real_imagery.authoritative_metadata_can_override_vision(
        museum,
        "Thoth ibis",
    ) is True
    assert real_imagery.authoritative_metadata_can_override_vision(
        pdf,
        "Egyptian papyrus",
    ) is False
    assert real_imagery.candidate_is_still_visual(museum) is True
    assert real_imagery.candidate_is_still_visual(pdf) is False


def test_subject_verification_falls_back_to_openrouter(monkeypatch):
    image = Image.new("RGB", (64, 64), "navy")
    monkeypatch.setattr(real_imagery, "_verify_subject_gemini", lambda *_args: None)
    monkeypatch.setattr(real_imagery, "_verify_subject_openrouter", lambda *_args: True)
    assert real_imagery._verify_subject(image, "Mars") is True


def test_gemini_rejection_does_not_ask_a_second_model(monkeypatch):
    image = Image.new("RGB", (64, 64), "navy")
    calls = []
    monkeypatch.setattr(real_imagery, "_verify_subject_gemini", lambda *_args: False)
    monkeypatch.setattr(
        real_imagery,
        "_verify_subject_openrouter",
        lambda *_args: calls.append(True),
    )
    assert real_imagery._verify_subject(image, "Mars") is False
    assert calls == []


def test_openrouter_verifier_sends_image_and_model_fallbacks(monkeypatch):
    image = Image.new("RGB", (64, 64), "navy")
    captured = {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_VISION_MODELS", "model/a, model/b")
    monkeypatch.setattr(real_imagery, "_OPENROUTER_VERIFIER_DISABLED", False)

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "YES"}}]},
        )

    monkeypatch.setattr(real_imagery.requests, "post", fake_post)
    assert real_imagery._verify_subject_openrouter(image, "Mars") is True
    assert captured["json"]["models"] == ["model/a", "model/b"]
    content = captured["json"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert captured["timeout"] == 20


def test_openrouter_rate_limit_trips_process_circuit_breaker(monkeypatch):
    image = Image.new("RGB", (64, 64), "navy")
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(real_imagery, "_OPENROUTER_VERIFIER_DISABLED", False)
    monkeypatch.setattr(
        real_imagery.requests,
        "post",
        lambda *_args, **_kwargs: calls.append(True) or SimpleNamespace(status_code=429),
    )
    assert real_imagery._verify_subject_openrouter(image, "Mars") is None
    assert real_imagery._verify_subject_openrouter(image, "Mars") is None
    assert calls == [True]


def test_a_failed_scene_reuses_story_imagery_instead_of_a_blank_gradient(monkeypatch):
    """A dead scene must never ship as a flat gradient card.

    Article 58 rendered a solid blue frame at 1.8s because both search and
    generation failed for one scene and the fallback drew a gradient. At two
    seconds that reads as a broken video, so any real image this story already
    earned is the better substitute.
    """
    found = Image.new("RGB", (1080, 1920), "navy")
    ImageDraw.Draw(found).ellipse((260, 400, 820, 960), fill="gold")

    def only_the_telescope_scene_finds_an_image(query, **_kwargs):
        if "telescope" not in str(query).lower():
            return None
        return SimpleNamespace(
            image=found,
            source_url="https://example.test/telescope.jpg",
            audit_record=lambda lane: {
                "lane": lane,
                "provider": "Wikimedia",
                "source_url": "https://example.test/telescope.jpg",
                "search_query": query,
            },
            license="Public domain",
            author="Archive",
            source_name="Archive",
        )

    monkeypatch.setattr(
        real_imagery, "fetch_referent_image", only_the_telescope_scene_finds_an_image
    )
    monkeypatch.setattr(video_generator, "search_pexels_images", lambda *_a, **_k: [])
    monkeypatch.setattr(video_generator, "_credit_photo", lambda image, _source: image)
    # Generation fails for every scene, which is what leaves a slot empty.
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: [None for _prompt in prompts],
    )

    records = []
    images = video_generator.generate_referent_scene_images(
        [
            {
                **_scene(
                    referent="object",
                    referent_query="telescope mirror",
                    evidence_query="telescope mirror array",
                    graphic_payload="",
                ),
                "_scene_index": 0,
            },
            {
                **_scene(
                    referent="object",
                    referent_query="unfindable subject",
                    evidence_query="unfindable subject entirely",
                    graphic_payload="",
                ),
                "_scene_index": 1,
            },
        ],
        article_title="A telescope story",
        visual_sources_out=records,
    )

    assert len(images) == 2
    gradient = video_generator.create_gradient_background()
    for image in images:
        assert ImageChops.difference(image.convert("RGB"), gradient).getbbox() is not None
    assert records[1]["editorial_reuse"] is True
    assert records[1]["provider"] == "Wikimedia"


def test_a_story_with_no_usable_imagery_fails_instead_of_rendering_blanks(monkeypatch):
    """Zero images for the whole story is a failed render, not a blank video."""
    monkeypatch.setattr(real_imagery, "fetch_referent_image", lambda *_a, **_k: None)
    monkeypatch.setattr(video_generator, "search_pexels_images", lambda *_a, **_k: [])
    monkeypatch.setattr(
        video_generator,
        "_parallel_image_gen",
        lambda prompts, **_kwargs: [None for _prompt in prompts],
    )

    try:
        video_generator.generate_referent_scene_images(
            [{**_scene(referent="object", referent_query="nothing"), "_scene_index": 0}],
            article_title="A story with no imagery",
        )
    except RuntimeError as error:
        assert "placeholder" in str(error)
    else:
        raise AssertionError("expected a RuntimeError instead of blank frames")

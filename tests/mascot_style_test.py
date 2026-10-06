"""Pixel Night Lab: every scene is pixel art generated from Moss's reference."""

from PIL import Image, ImageDraw

import mascot_style
import video_generator as vg
from visual_styles import DEFAULT_STYLE, is_mascot_style


def _scene(index, visual):
    return {"speech": f"Line {index}.", "visual": visual, "_scene_index": index}


def _pixel_frame(colour=(255, 61, 168)):
    image = Image.new("RGB", (1080, 1920), (11, 16, 38))
    ImageDraw.Draw(image).ellipse((300, 300, 780, 900), fill=colour)
    return image


def test_locked_default_routes_through_the_mascot_lane():
    assert is_mascot_style(DEFAULT_STYLE)
    assert not is_mascot_style("illustrated_science")
    assert mascot_style.CANONICAL_REFERENCE.exists()
    assert mascot_style.FALLBACK_REFERENCE.exists()


def test_moss_appears_in_the_hook_and_every_third_scene():
    assert mascot_style.moss_scene_indexes(10) == {0, 3, 6, 9}


def test_prompts_carry_the_locked_blocks_and_scene_content():
    hook = mascot_style.build_scene_prompt(
        {"visual": 'a meteorite slice labeled "CAI"'}, with_moss=True, is_hook=True
    )
    plain = mascot_style.build_scene_prompt(
        {"visual": "a protoplanetary disk"}, with_moss=False, is_hook=False
    )
    for block in (mascot_style.STYLE, mascot_style.CAPTION_BAND, mascot_style.AVOID,
                  mascot_style.MOSS_AVOID, mascot_style.MOSS):
        assert block in hook
    assert "CAI" not in hook  # lettering requests are stripped
    assert "amazed" in hook
    # The cheap model gets a positive-only prompt: no Moss, and no forbidden
    # nouns that it would draw ("never planets" produced a planet).
    assert mascot_style.PIXEL_LOOK in plain
    assert "moss" not in plain.lower() and "goggle" not in plain.lower()
    assert not any(word in plain.lower() for word in ("never", "avoid", "no mascot", "ring"))
    space = mascot_style.build_scene_prompt({"visual": "a magnetic field around a young star"}, with_moss=False, is_hook=False)
    assert mascot_style.SPACE_SCIENCE in space
    assert mascot_style.SPACE_SCIENCE not in plain


def test_scene_images_use_the_reference_and_fall_back_on_brand(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    calls = []
    monkeypatch.setattr("fal_client.upload_image", lambda *_a, **_k: "https://fal.test/moss.png")

    def fake_generate(prompt, reference_url):
        calls.append((prompt, reference_url))
        return None if "unrenderable" in prompt else _pixel_frame()

    monkeypatch.setattr(mascot_style, "_generate_one", fake_generate)
    records = []
    shots = [
        {**_scene(0, "a meteorite slice"), "_shot_step": 0},
        {**_scene(0, "a meteorite slice"), "_shot_step": 1},
        _scene(1, "an unrenderable subject"),
        _scene(2, "the young sun"),
    ]
    images = mascot_style.generate_mascot_scene_images(shots, visual_sources_out=records)

    assert len(images) == 4 and all(img.size == (1080, 1920) for img in images)
    assert len(calls) == 3  # one generation per scene, not per shot
    # Only Moss scenes carry his reference; others must not, or he leaks in.
    for prompt, url in calls:
        assert (url == "https://fal.test/moss.png") == (mascot_style.MOSS in prompt)
    assert sum(url is not None for _p, url in calls) == 1
    assert [r["provider"] for r in records] == ["FAL", "Moss reference", "FAL"]
    assert [r["moss"] for r in records] == [True, False, False]


def test_generate_video_uses_the_mascot_lane_for_the_default_style(monkeypatch, tmp_path):
    import wave

    class Routed(Exception):
        pass

    def fake_tts(_text, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000)
        return output_path

    def fake_mascot(shots, **_kwargs):
        raise Routed(len(shots))

    def photo_lane(*_a, **_k):
        raise AssertionError("archive photo lane used for the mascot style")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vg.tts_engine, "synthesize", fake_tts)
    monkeypatch.setattr(vg, "transcribe_word_timestamps", lambda *_a, **_k: [])
    monkeypatch.setattr("mascot_style.generate_mascot_scene_images", fake_mascot)
    monkeypatch.setattr(vg, "generate_referent_scene_images", photo_lane)

    try:
        vg.generate_video(
            article_id=1, title="T", script="One. Two.",
            scenes=[{"speech": "One.", "visual": "a"}, {"speech": "Two.", "visual": "b"}],
            image_source="mixed",
        )
    except Routed as routed:
        assert routed.args[0] >= 2
    else:
        raise AssertionError("mascot lane was not used")


def test_infographic_visuals_are_rewritten_before_generation(monkeypatch):
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    from types import SimpleNamespace
                    text = "Glowing red blood cells pile higher and higher beside a tiny clock."
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    monkeypatch.setattr(vg, "get_groq_client", lambda: Client)
    visual = "Timeline bar from day 0 to day 28 with a rising line labeled 'engraftment'."
    assert mascot_style.physicalize_visual(visual).startswith("Glowing red blood cells")
    # Already-physical scenes skip the model entirely.
    monkeypatch.setattr(vg, "get_groq_client", lambda: (_ for _ in ()).throw(AssertionError))
    assert mascot_style.physicalize_visual("A meteorite floats in space.") == "A meteorite floats in space."


def test_rewrite_falls_back_to_the_original_without_groq(monkeypatch):
    monkeypatch.setattr(vg, "get_groq_client", lambda: None)
    visual = "A bar chart comparing two fields."
    assert mascot_style.physicalize_visual(visual) == visual


def test_truncated_rewrite_is_rejected(monkeypatch):
    from types import SimpleNamespace

    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content="Numerous clumps of diseased blood cells are envelop")
                    )])

    monkeypatch.setattr(vg, "get_groq_client", lambda: Client)
    visual = "A chart of 30 patients."
    assert mascot_style.physicalize_visual(visual) == visual


def test_cost_estimate_prices_moss_and_plain_scenes_separately():
    # 10 scenes: 4 Moss scenes on the edit model, 6 on the cheap model.
    expected = 4 * mascot_style.MASCOT_IMAGE_COST_USD + 6 * mascot_style.SCENE_IMAGE_COST_USD
    assert abs(mascot_style.estimate_mascot_cost(10) - expected) < 1e-9


def test_plain_scenes_use_the_cheap_text_model(monkeypatch):
    seen = {}

    def fake_fal(prompt, *, model=None, num_inference_steps=None):
        seen["model"] = model
        return _pixel_frame()

    monkeypatch.setattr(vg, "generate_image_fal", fake_fal)
    image = mascot_style._generate_one("a red blood cell", None)
    assert seen["model"] == mascot_style.SCENE_IMAGE_MODEL
    assert image.size == (1080, 1920)

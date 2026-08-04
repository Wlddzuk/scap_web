"""Regression tests for Clipper's automatic Illustrated Science identity."""

import json
from types import SimpleNamespace

from PIL import Image
import pytest

import summarizer
import video_generator
import visual_styles


def _summary_payload():
    return {
        "tldr": "A source-backed science summary.",
        "bullets": ["One fact."],
        "video_script": "A whale can recycle oxygen inside its body. Would you dive beside it?",
        "hashtags": ["#Whales", "#Oxygen", "#Science"],
        "hook_variants": [
            "This whale carries its own oxygen reserve.",
            "A hidden organ keeps this whale underwater.",
            "Where does a diving whale store oxygen?",
        ],
        "best_hook_index": 0,
        "dominant_emotion": "curious",
        "suggested_style": "documentary",
        "cover_line": "THE HIDDEN OXYGEN RESERVE",
        "cta_question": "Would you dive beside it?",
        "search_caption": "How whales store oxygen during long dives.",
        "series_lane": "other",
        "scenes": [
            {
                "speech": "A whale can recycle oxygen inside its body.",
                "visual": "a whale cutaway reveals a highlighted lung",
                "emotion": "curious",
                "focus_label": "OXYGEN RESERVE EXTRA WORDS",
                "visual_action": "reveal",
                "referent": "unphotographable",
                "referent_query": "must be cleared",
                "precise_claim": True,
                "graphic_payload": "oxygen reserve",
            },
            {
                "speech": "Would you dive beside it?",
                "visual": "a whale descends through clear blue water",
                "emotion": "curious",
                "visual_action": "made-up",
                "referent": "object",
                "referent_query": "diving whale underwater",
                "precise_claim": False,
                "graphic_payload": "must be cleared",
            },
        ],
    }


def test_illustrated_science_is_the_automatic_first_render_style():
    assert visual_styles.DEFAULT_STYLE == "illustrated_science"
    assert visual_styles.list_styles()[0]["key"] == "illustrated_science"
    assert visual_styles.auto_pick_style("Any science story", "Any script") == (
        "illustrated_science"
    )
    prompt = visual_styles.apply_style("a whale cutaway", "illustrated_science")
    assert "warm off-white paper" in prompt
    assert "cobalt-blue" in prompt
    assert "no photorealism" in prompt


def test_summary_parser_forces_brand_style_and_keeps_teaching_metadata():
    parsed = summarizer.parse_response(json.dumps(_summary_payload()))

    assert parsed["suggested_style"] == "illustrated_science"
    assert parsed["scenes"][0]["focus_label"] == "OXYGEN RESERVE EXTRA WORDS"
    assert parsed["scenes"][0]["visual_action"] == "reveal"
    assert parsed["scenes"][0]["referent_query"] == ""
    assert parsed["scenes"][0]["precise_claim"] is True
    assert parsed["scenes"][1]["graphic_payload"] == ""
    assert parsed["scenes"][1]["visual_action"] == "highlight"


def test_scene_plan_numbers_progressive_teaching_steps():
    scene = {
        "speech": " ".join(["oxygen"] * 30),
        "visual": "a whale cutaway",
        "focus_label": "OXYGEN STORE",
        "visual_action": "reveal",
    }
    plan = video_generator.build_scene_shot_plan([scene], 6.0)

    assert [shot["_shot_step"] for shot in plan] == list(range(len(plan)))
    assert all(shot["_scene_shot_count"] == len(plan) for shot in plan)


def test_illustrated_scene_generation_reuses_one_image_per_scene(monkeypatch):
    shots = [
        {"visual": "whale", "_scene_index": 0, "_shot_type": "wide"},
        {"visual": "whale", "_scene_index": 0, "_shot_type": "macro"},
        {"visual": "lung", "_scene_index": 1, "_shot_type": "wide"},
        {"visual": "lung", "_scene_index": 1, "_shot_type": "macro"},
    ]
    captured = {}

    def generate(prompts, *, premium_flags=None):
        captured["prompts"] = prompts
        captured["premium_flags"] = premium_flags
        return [f"image-{index}" for index in range(len(prompts))]

    monkeypatch.setattr(video_generator, "_parallel_image_gen", generate)
    images = video_generator.generate_scene_images(
        shots,
        "illustrated_science",
        image_source="ai",
    )

    assert len(captured["prompts"]) == 2
    assert images == ["image-0", "image-0", "image-1", "image-1"]


def test_referent_hook_reuses_opening_visual_without_generation(monkeypatch):
    calls = []

    def generate(prompt, **kwargs):
        calls.append((prompt, kwargs))
        return Image.new("RGB", (20, 20), (244, 240, 230))

    monkeypatch.setattr(video_generator, "generate_image_fal", generate)
    monkeypatch.setattr(video_generator, "create_clip", lambda _image, duration, **_kwargs: SimpleNamespace(duration=duration))

    clips = video_generator.create_hook_clips(
        "A whale stores oxygen",
        duration=4.0,
        style_key="illustrated_science",
        use_video_hook=False,
        opening_image=Image.new("RGB", (20, 20), (244, 240, 230)),
    )

    assert calls == []
    assert len(clips) == 4


def test_scene_render_budget_prices_one_premium_fallback_per_unique_scene():
    shots = [
        {"_scene_index": 0},
        {"_scene_index": 0},
        {"_scene_index": 1},
        {"_scene_index": 2},
        {"_scene_index": 2},
    ]

    estimated = video_generator.estimate_planned_still_cost(
        shots,
        use_scenes=True,
        image_source="ai",
        style_key="illustrated_science",
    )

    assert estimated == pytest.approx(
        video_generator.estimate_ai_still_cost(3, 0)
    )

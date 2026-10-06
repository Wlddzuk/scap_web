"""Pixel Night Lab: scene images, Moss's animation rules, and one real render."""

import wave
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw

import moss_sprite
import pixel_scenes
import video_generator as vg
from visual_styles import DEFAULT_STYLE, strip_lettering_requests


def _pixel_frame(colour=(255, 61, 168)):
    image = Image.new("RGB", (1080, 1920), (11, 16, 38))
    ImageDraw.Draw(image).ellipse((300, 300, 780, 900), fill=colour)
    return image


# --- the locked style -------------------------------------------------------

def test_pixel_night_lab_is_the_only_style():
    assert DEFAULT_STYLE == "pixel_night_lab" == pixel_scenes.STYLE_KEY
    assert strip_lettering_requests('a vial labeled "H5N1" glowing') == "a vial glowing"


def test_scene_prompt_is_positive_only():
    # The scene model has no negative prompt: "never planets" drew a planet.
    prompt = pixel_scenes.build_scene_prompt({"visual": 'a stem cell labeled "CD34" splitting'})
    assert pixel_scenes.PIXEL_LOOK in prompt
    assert "CD34" not in prompt
    assert not any(word in prompt.lower() for word in ("never", "avoid", "ring", "moss", "mascot"))
    assert pixel_scenes.SPACE_SCIENCE not in prompt
    space = pixel_scenes.build_scene_prompt({"visual": "a magnetic field around a young star"})
    assert pixel_scenes.SPACE_SCIENCE in space


def _groq_replying(text):
    class Client:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    return Client


def test_infographic_visuals_are_rewritten(monkeypatch):
    monkeypatch.setattr(vg, "get_groq_client", lambda: _groq_replying("Red blood cells pile up beside a glowing marrow cavity."))
    visual = "Timeline bar from day 0 to day 28 labeled 'engraftment'."
    assert pixel_scenes.physicalize_visual(visual).startswith("Red blood cells")
    # Physical scenes never reach the model.
    monkeypatch.setattr(vg, "get_groq_client", lambda: (_ for _ in ()).throw(AssertionError))
    assert pixel_scenes.physicalize_visual("A meteorite floats in space.") == "A meteorite floats in space."


def test_rewrite_falls_back_when_unavailable_or_truncated(monkeypatch):
    visual = "A bar chart comparing two fields."
    monkeypatch.setattr(vg, "get_groq_client", lambda: None)
    assert pixel_scenes.physicalize_visual(visual) == visual
    monkeypatch.setattr(vg, "get_groq_client", lambda: _groq_replying("Clumps of diseased cells are envelop"))
    assert pixel_scenes.physicalize_visual(visual) == visual


def _shots(*visuals):
    return [{"speech": f"Line {i}.", "visual": v, "_scene_index": i, "_shot_step": 0} for i, v in enumerate(visuals)]


def test_one_image_per_scene_and_nearest_reuse_on_failure(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    monkeypatch.setattr(vg, "get_groq_client", lambda: None)
    calls = []

    def fake_fal(prompt, *, model=None, num_inference_steps=None):
        calls.append(model)
        return vg.create_gradient_background() if "broken" in prompt else _pixel_frame()

    monkeypatch.setattr(vg, "generate_image_fal", fake_fal)
    shots = _shots("a meteorite", "a broken subject", "a young star")
    shots.insert(1, {**shots[0], "_shot_step": 1})          # scene 0 has two shots
    records = []
    images = pixel_scenes.generate_scene_images(shots, visual_sources_out=records)
    assert len(images) == 4 and all(image.size == (1080, 1920) for image in images)
    assert calls == [pixel_scenes.SCENE_IMAGE_MODEL] * 3     # per scene, not per shot
    assert [r.get("reused_from") for r in records] == [None, 0, None]


def test_no_generated_scene_refuses_to_render(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    monkeypatch.setattr(vg, "get_groq_client", lambda: None)
    monkeypatch.setattr(vg, "generate_image_fal", lambda *a, **k: vg.create_gradient_background())
    with pytest.raises(RuntimeError, match="No scene image"):
        pixel_scenes.generate_scene_images(_shots("a cell"))


# --- Moss ---------------------------------------------------------------------

def test_moss_hosts_the_hook_every_other_scene_and_the_last():
    spans = [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20)]
    appearances = moss_sprite.plan_appearances(spans, 20.0)
    assert [(a.start, a.end) for a in appearances] == [(0, 4), (8, 12), (16, 20)]
    assert [a.side for a in appearances] == ["left", "right", "left"]
    assert appearances[-1].leave is False          # he stays to the end


def test_reactions_follow_the_narration():
    words = [
        {"text": "It", "start": 2.0, "end": 2.2},
        {"text": "was", "start": 2.2, "end": 2.4},
        {"text": "12", "start": 2.4, "end": 2.7},
        {"text": "times", "start": 2.7, "end": 3.0},
        {"text": "stronger.", "start": 3.0, "end": 3.4},
        {"text": "Could", "start": 5.0, "end": 5.2},
        {"text": "it", "start": 5.2, "end": 5.3},
        {"text": "work?", "start": 5.3, "end": 5.7},
        {"text": "Follow", "start": 7.0, "end": 7.3},
    ]
    poses = {(r.pose, round(r.start, 1)) for r in moss_sprite.plan_reactions(words, hook_len=4.0)}
    assert ("amazed", 0.2) in poses                 # the hook reaction
    assert ("amazed", 2.4) in poses                 # the number
    assert ("thinking", 5.0) in poses               # the whole question sentence
    assert ("wave", 7.0) in poses                   # the CTA


def test_moss_walks_in_stays_above_the_captions_and_leaves():
    appearances = [moss_sprite.Appearance(1.0, 5.0, "left")]
    reactions = []
    assert moss_sprite.moss_state(0.5, appearances, reactions) is None
    entering = moss_sprite.moss_state(1.1, appearances, reactions)
    settled = moss_sprite.moss_state(2.5, appearances, reactions)
    assert entering[0] in {"walk_a", "walk_b"} and entering[1] < settled[1]
    assert settled[1] == moss_sprite.EDGE_MARGIN
    assert moss_sprite.moss_state(4.8, appearances, reactions)[0] in {"walk_a", "walk_b"}
    assert moss_sprite.moss_state(5.2, appearances, reactions) is None


def test_overlay_never_enters_the_caption_band():
    overlay = moss_sprite.create_moss_overlay(8.0, [(0, 4), (4, 8)], [], hook_len=3.0)
    caption_band_top = int(vg.VIDEO_HEIGHT * 0.62)
    for t in np.arange(0, 8.0, 1 / moss_sprite.SPRITE_FPS):
        left, top = overlay.pos(t)
        alpha = overlay.mask.get_frame(t)
        rows = np.nonzero(alpha.max(axis=1) > 0)[0]
        if rows.size:
            assert top + rows.max() <= caption_band_top, t


# --- one real render -----------------------------------------------------------

def test_render_has_moss_captions_and_pixel_scenes(monkeypatch, tmp_path):
    from moviepy.editor import VideoFileClip

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_ENABLED", "false")

    def fake_tts(_text, output_path, **_kwargs):
        with wave.open(output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 16000 * 3)
        return output_path

    words = [{"text": w, "start": 0.3 + i * 0.5, "end": 0.7 + i * 0.5}
             for i, w in enumerate("Cells shield blood now".split())]
    monkeypatch.setattr(vg.tts_engine, "synthesize", fake_tts)
    monkeypatch.setattr(vg, "transcribe_word_timestamps", lambda *a, **k: words)
    monkeypatch.setattr("pixel_scenes.generate_scene_images",
                        lambda shots, **k: [_pixel_frame() for _ in shots])

    path = vg.generate_video(7, "Cells shield blood", "Cells shield blood now.",
                             scenes=[{"speech": "Cells shield blood now.", "visual": "cells"}])
    clip = VideoFileClip(path)
    try:
        assert clip.size == [1080, 1920] and abs(clip.duration - 3.0) < 0.2
        frame = clip.get_frame(2.0)
        # Moss's mint body is on screen, standing above the caption band.
        # Mint is green-tinted (G well above R); grey caption anti-aliasing is not.
        r, g = frame[..., 0].astype(int), frame[..., 1].astype(int)
        mint = (g - r > 30) & (g > 170)
        ys, _xs = np.nonzero(mint)
        # H.264 chroma noise at caption edges adds a few stray tinted pixels.
        assert ys.size > 500 and np.percentile(ys, 99.5) <= int(1920 * 0.62)
    finally:
        clip.close()

"""Rendering regressions using real MoviePy composition and no remote services."""
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import video_generator as vg


def test_hook_and_body_keep_original_shot_alignment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    monkeypatch.setattr(vg, "VIDEO_WIDTH", 32)
    monkeypatch.setattr(vg, "VIDEO_HEIGHT", 56)
    monkeypatch.setattr(vg.tts_engine, "synthesize", lambda *a, **kw: "voice.wav")
    monkeypatch.setattr(vg, "AudioFileClip", lambda path: SimpleNamespace(
        duration=12.0, close=lambda: None
    ))
    monkeypatch.setattr(vg, "transcribe_word_timestamps", lambda *a, **kw: [])
    monkeypatch.setattr(vg, "create_headline_clip", lambda *a, **kw: None)
    monkeypatch.setattr("moss_sprite.create_moss_overlay", lambda *a, **kw: None)
    scenes = [
        {"speech": "one two three four five six", "visual": "first scene"},
        {"speech": "seven eight nine ten eleven twelve", "visual": "second scene"},
    ]
    script = " ".join(scene["speech"] for scene in scenes)
    full_plan = vg.build_scene_shot_plan(scenes, 12.0)
    images = [Image.new("RGB", (32, 56), (100 + i * 10, 80, 60)) for i in range(len(full_plan))]
    monkeypatch.setattr("pixel_scenes.generate_scene_images", lambda *a, **kw: images)
    created_images = []
    original_create = vg.create_clip

    def capture_clip(image, *args, **kwargs):
        created_images.append(image)
        return original_create(image, *args, **kwargs)

    monkeypatch.setattr(vg, "create_clip", capture_clip)
    encoded = {}
    monkeypatch.setattr(vg.VideoClip, "write_videofile", lambda self, *a, **kw:
                        encoded.update(duration=self.duration, **kw))

    vg.generate_video(1, "A discovery", script, scenes=scenes, captions=False)

    # The hook cuts across the opening scenes, then the body resumes at the
    # shot on screen when the hook ends, in plan order.
    hook = created_images[:vg.NUM_HOOK_IMAGES]
    assert hook[0] is images[0]
    body = created_images[vg.NUM_HOOK_IMAGES:]
    hook_duration = min(vg.HOOK_DURATION, max(2.0, 12.0 * 0.25))
    body_slots = vg.split_plan_at_hook(full_plan, hook_duration)
    assert body_slots[0][0] > 0
    assert [id(image) for image in body] == [id(images[slot]) for slot, _ in body_slots]
    assert encoded["duration"] == pytest.approx(12.0)
    assert encoded["ffmpeg_params"][-2:] == ["-movflags", "+faststart"]


def _timed(words_with_starts):
    return [
        {"text": text, "start": start, "end": start + 0.3}
        for text, start in words_with_starts
    ]


def test_scene_shots_start_when_their_first_word_is_spoken():
    scenes = [
        {"speech": "Your blood proteins rebuild.", "visual": "a"},
        {"speech": "Scientists monitored twelve volunteers.", "visual": "b"},
        {"speech": "They sampled blood daily.", "visual": "c"},
    ]
    # A long pause before scene 2 and trailing silence: a word-count split
    # would cut to each image well before its narration.
    timed_words = _timed([
        ("Your", 0.1), ("blood", 0.4), ("proteins", 0.7), ("rebuild.", 1.1),
        ("Scientists", 3.0), ("monitored", 3.6), ("twelve", 4.1), ("volunteers.", 4.5),
        ("They", 7.2), ("sampled", 7.5), ("blood", 7.9), ("daily.", 8.2),
    ])
    total = 10.0

    plan = vg.build_scene_shot_plan(scenes, total, timed_words)
    spans = vg.scene_spans(plan)

    assert [start for start, _ in spans] == pytest.approx([0.0, 3.0, 7.2])
    assert spans[-1][1] == pytest.approx(total)
    assert max(shot["_duration"] for shot in plan) <= vg.MAX_SHOT_DURATION + 1e-9
    # The word-count split is what put pictures ahead of their narration.
    assert vg.scene_spans(vg.build_scene_shot_plan(scenes, total))[2][0] < 7.2 - 0.5


def test_scene_starts_fall_back_to_word_count_without_usable_timings():
    scenes = [
        {"speech": "one two three", "visual": "a"},
        {"speech": "four five six", "visual": "b"},
    ]
    word_count = vg.build_scene_shot_plan(scenes, 6.0)
    for timed_words in ([], _timed([("unrelated", 0.0), ("noise", 1.0)])):
        plan = vg.build_scene_shot_plan(scenes, 6.0, timed_words)
        assert vg.scene_spans(plan) == pytest.approx(vg.scene_spans(word_count))


def test_scene_start_tolerates_misheard_opening_word():
    scenes = [
        {"speech": "alpha beta gamma", "visual": "a"},
        {"speech": "delta epsilon zeta", "visual": "b"},
    ]
    # Whisper mangled "delta"; the scene anchors on its next matched word.
    timed_words = _timed([
        ("alpha", 0.0), ("beta", 0.5), ("gamma", 1.0),
        ("dealt", 2.4), ("epsilon", 2.8), ("zeta", 3.2),
    ])
    spans = vg.scene_spans(vg.build_scene_shot_plan(scenes, 4.0, timed_words))
    assert spans[1][0] == pytest.approx(2.8)

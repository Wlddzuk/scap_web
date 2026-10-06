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

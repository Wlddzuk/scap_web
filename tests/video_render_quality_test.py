"""Rendering regressions using real MoviePy composition and no remote services."""
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import video_generator as vg


@pytest.mark.parametrize("scene_mode", [True, False])
def test_hook_and_body_keep_color_and_original_shot_alignment(
    monkeypatch, tmp_path, scene_mode
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    monkeypatch.setattr(vg, "VIDEO_WIDTH", 32)
    monkeypatch.setattr(vg, "VIDEO_HEIGHT", 56)
    monkeypatch.setattr(vg.tts_engine, "synthesize", lambda *a, **kw: "voice.wav")
    monkeypatch.setattr(vg, "AudioFileClip", lambda path: SimpleNamespace(
        duration=12.0, close=lambda: None
    ))
    monkeypatch.setattr(vg, "create_headline_clip", lambda *a, **kw: None)
    scenes = [
        {"speech": "one two three four five six", "visual": "first scene"},
        {"speech": "seven eight nine ten eleven twelve", "visual": "second scene"},
    ]
    script = " ".join(scene["speech"] for scene in scenes)
    full_plan = (vg.build_scene_shot_plan(scenes, 12.0) if scene_mode else
                 vg.build_legacy_shot_plan(vg.chunk_text(script), 12.0))
    images = [Image.new("RGB", (32, 56), (100 + i * 10, 80, 60))
              for i in range(len(full_plan))]
    monkeypatch.setattr(vg, "generate_referent_scene_images", lambda *a, **kw: images)
    monkeypatch.setattr(vg, "generate_scene_images", lambda *a, **kw: images)
    created_images = []
    original_create = vg.create_clip

    def capture_clip(image, *args, **kwargs):
        created_images.append(image)
        return original_create(image, *args, **kwargs)

    monkeypatch.setattr(vg, "create_clip", capture_clip)
    original_hook = vg.create_hook_clips

    def hook(*args, **kwargs):
        if scene_mode:
            return original_hook(*args, **kwargs)
        # Legacy hooks normally fetch their own imagery; isolate that service.
        return [vg.ImageClip(np.asarray(images[0])).set_duration(
            kwargs["duration"] / vg.NUM_HOOK_IMAGES
        ) for _ in range(vg.NUM_HOOK_IMAGES)]

    monkeypatch.setattr(vg, "create_hook_clips", hook)
    encoded = {}
    monkeypatch.setattr(vg.VideoClip, "write_videofile", lambda self, *a, **kw:
                        encoded.update(duration=self.duration, **kw))

    vg.generate_video(1, "A discovery", script, scenes=scenes if scene_mode else None,
                      image_source="mixed", captions=False, use_video_hook=False,
                      color_intensity="electric", style_key="illustrated_science")

    if scene_mode:
        # Opening frames are graded once, exactly like body frames.
        expected_hook = vg.apply_color_intensity(images[0], "electric")
        np.testing.assert_array_equal(created_images[0], expected_hook)
        created_images = created_images[vg.NUM_HOOK_IMAGES:]
    hook_duration = min(vg.HOOK_DURATION, max(2.0, 12.0 * 0.25))
    body_slots = vg.split_plan_at_hook(full_plan, hook_duration)
    assert body_slots[0][0] > 0
    assert len(created_images) == len(body_slots)
    for image, (slot, _) in zip(created_images, body_slots):
        np.testing.assert_array_equal(image, vg.apply_color_intensity(images[slot], "electric"))
    assert encoded["duration"] == pytest.approx(12.0)
    assert encoded["ffmpeg_params"][-2:] == ["-movflags", "+faststart"]

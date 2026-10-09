"""Retention-pipeline regressions for cover packaging, pacing and rendering."""

import inspect
from io import BytesIO
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time

import numpy as np
from PIL import Image, ImageDraw
import pytest

import video_generator


class _FakeOverlayClip:
    def set_start(self, _value):
        return self

    def set_duration(self, _value):
        return self

    def set_position(self, _value):
        return self


def test_cover_line_is_large_two_line_copy_and_hard_capped_at_five_words(
    monkeypatch,
):
    captured = {}

    def render(text, **kwargs):
        captured.update({"text": text, **kwargs})
        return Image.new("RGBA", (2, 2))

    monkeypatch.setattr(video_generator, "render_text_overlay", render)
    monkeypatch.setattr(
        video_generator,
        "ImageClip",
        lambda *_args, **_kwargs: _FakeOverlayClip(),
    )

    video_generator.create_headline_clip(
        "The old article title wraps across far too many lines",
        2.5,
        cover_line="one planet changes every search today",
    )

    assert captured["text"] == "ONE PLANET CHANGES EVERY SEARCH"
    assert captured["font_size"] >= 140
    assert captured["max_lines"] == 2


def test_cover_line_falls_back_to_first_five_title_words(monkeypatch):
    captured = {}

    def render(text, **_kwargs):
        captured["text"] = text
        return Image.new("RGBA", (2, 2))

    monkeypatch.setattr(video_generator, "render_text_overlay", render)
    monkeypatch.setattr(
        video_generator,
        "ImageClip",
        lambda *_args, **_kwargs: _FakeOverlayClip(),
    )

    video_generator.create_headline_clip(
        "Astronomers found a strange atmosphere around another world",
        2.5,
    )

    assert captured["text"] == "ASTRONOMERS FOUND A STRANGE ATMOSPHERE"


def test_cover_line_tries_smaller_type_before_truncating(monkeypatch):
    captured = {}
    original = ImageDraw.ImageDraw.multiline_text

    def record_text(draw, xy, text, *args, **kwargs):
        captured["text"] = text
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", record_text)

    video_generator.render_text_overlay(
        "JUPITER’S STORM IS SHRINKING",
        max_width=video_generator.VIDEO_WIDTH - 80,
        font_size=144,
        min_font_size=96,
        stroke_width=9,
        padding=26,
        max_lines=2,
    )

    assert captured["text"].replace("\n", " ") == (
        "JUPITER’S STORM IS SHRINKING"
    )


@pytest.mark.parametrize(
    "headline",
    [
        "EXTRATERRESTRIAL DISCOVERY CHANGES HUMANITY FOREVER",
        "SUPERCALIFRAGILISTICEXPIALIDOCIOUSSUPERCALIFRAGILISTIC",
    ],
)
def test_cover_overlay_never_disappears_when_copy_is_too_wide(
    monkeypatch,
    headline,
):
    captured = {}
    original = ImageDraw.ImageDraw.multiline_text

    def record_text(draw, xy, text, *args, **kwargs):
        captured["text"] = text
        return original(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "multiline_text", record_text)

    overlay = video_generator.render_text_overlay(
        headline,
        max_width=video_generator.VIDEO_WIDTH - 80,
        font_size=144,
        min_font_size=96,
        stroke_width=9,
        padding=26,
        max_lines=2,
    )

    assert captured["text"].strip()
    assert len(captured["text"].splitlines()) <= 2
    assert overlay.getbbox() is not None
    assert overlay.width <= video_generator.VIDEO_WIDTH - 80


def _mean_hsv_saturation(image):
    pixels = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    maximum = pixels.max(axis=2)
    minimum = pixels.min(axis=2)
    saturation = np.divide(
        maximum - minimum,
        maximum,
        out=np.zeros_like(maximum),
        where=maximum > 0,
    )
    return float(saturation.mean())


def test_scene_plans_never_exceed_shot_cap_and_preserve_time():
    scenes = [
        {
            "speech": " ".join(["discovery"] * 90),
            "visual": "a telescope tracks a distant planet",
        },
        {
            "speech": " ".join(["payoff"] * 30),
            "visual": "a planet crosses its star",
        },
    ]

    scene_plan = video_generator.build_scene_shot_plan(scenes, 11.7)
    short_plan = video_generator.build_scene_shot_plan(
        [{"speech": "one short beat", "visual": "one short beat"}], 9.1
    )

    for plan, expected_total in ((scene_plan, 11.7), (short_plan, 9.1)):
        durations = [shot["_duration"] for shot in plan]
        assert sum(durations) == pytest.approx(expected_total)
        assert max(durations) <= video_generator.MAX_SHOT_DURATION
        assert [shot["_shot_type"] for shot in plan] == [
            video_generator.SHOT_TYPES[index % len(video_generator.SHOT_TYPES)]
            for index in range(len(plan))
        ]


def test_fraction_just_over_cap_is_split_strictly():
    pieces = video_generator.split_shot_duration(
        video_generator.MAX_SHOT_DURATION + 1e-10
    )

    assert len(pieces) == 2
    assert max(pieces) <= video_generator.MAX_SHOT_DURATION


def test_render_paths_are_unique_and_narration_stays_outside_static(tmp_path):
    videos_dir = tmp_path / "static" / "videos"

    first_output, first_audio = video_generator.allocate_render_paths(
        42, videos_dir
    )
    second_output, second_audio = video_generator.allocate_render_paths(
        42, videos_dir
    )

    assert first_output != second_output
    assert first_audio != second_audio
    assert first_output.parent == videos_dir
    assert second_output.parent == videos_dir
    assert first_audio.suffix == ".wav"
    assert second_audio.suffix == ".wav"
    assert not first_audio.is_relative_to(videos_dir)
    assert not second_audio.is_relative_to(videos_dir)


def test_partial_narration_is_removed_when_tts_raises(monkeypatch, tmp_path):
    attempted_paths = []

    def fail_after_partial_write(_text, output_path, **_kwargs):
        path = Path(output_path)
        path.write_bytes(b"partial narration")
        attempted_paths.append(path)
        raise RuntimeError("synthetic TTS failure")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        video_generator.tts_engine,
        "synthesize",
        fail_after_partial_write,
    )

    with pytest.raises(RuntimeError, match="synthetic TTS failure"):
        video_generator.generate_video(
            article_id=42,
            title="Synthetic test",
            script="This request stops before any external image call.",
        )

    assert len(attempted_paths) == 1
    assert not attempted_paths[0].exists()
    assert not attempted_paths[0].is_relative_to(
        tmp_path / "static" / "videos"
    )


def test_fal_cdn_retry_does_not_repeat_successful_paid_inference(monkeypatch):
    inference_calls = []
    download_calls = []
    encoded = BytesIO()
    Image.new("RGB", (4, 4), "red").save(encoded, format="PNG")

    def run(model, **kwargs):
        inference_calls.append((model, kwargs))
        return {"images": [{"url": "https://cdn.example.test/generated.png"}]}

    class _Response:
        content = encoded.getvalue()

        def raise_for_status(self):
            return None

    def download(url, **kwargs):
        download_calls.append((url, kwargs))
        if len(download_calls) == 1:
            raise TimeoutError("synthetic CDN timeout")
        return _Response()

    monkeypatch.setenv("FAL_KEY", "synthetic-test-key")
    monkeypatch.setitem(sys.modules, "fal_client", SimpleNamespace(run=run))
    monkeypatch.setattr(video_generator.requests, "get", download)
    monkeypatch.setattr(video_generator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        video_generator,
        "resize_and_crop_image",
        lambda image, _width, _height: image,
    )

    image = video_generator.generate_image_fal("synthetic space image")

    assert image.size == (4, 4)
    assert len(inference_calls) == 1
    assert len(download_calls) == 2


def test_subframe_final_padding_is_an_explicit_capped_clip():
    class _PaddingClip:
        def __init__(self):
            self.duration = None

        def set_duration(self, duration):
            self.duration = duration
            return self

    class _MainVideo:
        duration = 2.49

        def __init__(self):
            self.frame_times = []

        def to_ImageClip(self, t):
            self.frame_times.append(t)
            return _PaddingClip()

    main_video = _MainVideo()
    clips = [SimpleNamespace(duration=2.49)]
    padding = video_generator.create_final_padding_clips(main_video, 0.02)
    clips.extend(padding)

    assert len(padding) == 1
    assert padding[0].duration == pytest.approx(0.02)
    assert max(clip.duration for clip in clips) <= (
        video_generator.MAX_SHOT_DURATION
    )

"""Retention-pipeline regressions for cover packaging, pacing, and imagery."""

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

import real_imagery
import video_generator
import visual_styles


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


def test_color_intensity_presets_are_public_stable_and_normalized():
    assert list(video_generator.COLOR_INTENSITY_PRESETS) == [
        "natural",
        "vivid",
        "electric",
    ]
    assert video_generator.DEFAULT_COLOR_INTENSITY == "vivid"
    assert [option["id"] for option in video_generator.color_intensity_options()] == [
        "natural",
        "vivid",
        "electric",
    ]
    assert video_generator.normalize_color_intensity(" ELECTRIC ") == "electric"
    assert video_generator.normalize_color_intensity(None) == "vivid"
    assert video_generator.normalize_color_intensity("unknown") == "vivid"


def test_natural_preserves_source_and_electric_is_stronger_than_vivid():
    source = Image.fromarray(
        np.asarray(
            [
                [(154, 94, 82), (75, 136, 106), (66, 103, 159), (128, 128, 128)],
                [(174, 112, 96), (86, 155, 124), (78, 120, 180), (170, 170, 170)],
            ],
            dtype=np.uint8,
        ),
        mode="RGB",
    )

    natural = video_generator.apply_color_intensity(source, "natural")
    vivid = video_generator.apply_color_intensity(source, "vivid")
    electric = video_generator.apply_color_intensity(source, "electric")

    assert natural is source
    assert np.array_equal(np.asarray(natural), np.asarray(source))
    assert _mean_hsv_saturation(vivid) > _mean_hsv_saturation(natural)
    assert _mean_hsv_saturation(electric) > _mean_hsv_saturation(vivid) + 0.05
    assert np.asarray(electric).max() <= 255


def test_electric_palette_guidance_reaches_still_and_motion_prompts(monkeypatch):
    still_prompts = []
    motion_prompts = []
    shots = [
        {
            "speech": "Opening beat",
            "visual": "a telescope beneath the stars",
            "_scene_index": 0,
            "_shot_type": "macro close-up",
        },
        {
            "speech": "The discovery lands!",
            "visual": "a newly found planet",
            "_scene_index": 1,
            "_shot_type": "wide establishing shot",
            "emotion": "awe",
        },
    ]

    monkeypatch.setattr(
        visual_styles,
        "apply_style",
        lambda prompt, *_args, **_kwargs: prompt,
    )

    def generate_stills(prompts, **_kwargs):
        still_prompts.extend(prompts)
        return [Image.new("RGB", (2, 2)) for _prompt in prompts]

    monkeypatch.setattr(video_generator, "_parallel_image_gen", generate_stills)
    video_generator.generate_scene_images(
        shots,
        "cinematic",
        color_intensity="electric",
    )

    monkeypatch.setattr(
        video_generator,
        "generate_motion_video_fal",
        lambda prompt, *_args, **_kwargs: motion_prompts.append(prompt),
    )
    video_generator.create_body_motion_clips(
        shots,
        durations=[1.0, 1.0],
        style_key="cinematic",
        video_model="test-model",
        clip_limit=1,
        color_intensity="electric",
    )

    assert still_prompts
    assert motion_prompts
    assert all("intense cyan" in prompt for prompt in still_prompts)
    assert all("hot magenta" in prompt for prompt in still_prompts)
    assert all("intense cyan" in prompt for prompt in motion_prompts)
    assert all("hot magenta" in prompt for prompt in motion_prompts)


def test_electric_palette_guidance_reaches_hook_video_and_fallback_stills(
    monkeypatch,
):
    hook_motion_prompts = []
    hook_still_prompts = []

    monkeypatch.setattr(
        visual_styles,
        "apply_style",
        lambda prompt, *_args, **_kwargs: prompt,
    )

    def unavailable_hook_video(prompt, _model):
        hook_motion_prompts.append(prompt)
        return None

    def generate_hook_still(prompt, **_kwargs):
        hook_still_prompts.append(prompt)
        return Image.new("RGB", (2, 2), (80, 120, 160))

    monkeypatch.setattr(
        video_generator,
        "generate_hook_video_fal",
        unavailable_hook_video,
    )
    monkeypatch.setattr(
        video_generator,
        "generate_image_fal",
        generate_hook_still,
    )
    monkeypatch.setattr(
        video_generator,
        "create_clip",
        lambda _image, duration, **_kwargs: SimpleNamespace(duration=duration),
    )

    clips = video_generator.create_hook_clips(
        "A planet appears",
        duration=4.0,
        style_key="cinematic",
        use_video_hook=True,
        color_intensity="electric",
    )

    assert len(clips) == video_generator.NUM_HOOK_IMAGES
    assert len(hook_motion_prompts) == 1
    assert len(hook_still_prompts) == video_generator.NUM_HOOK_IMAGES
    assert "intense cyan" in hook_motion_prompts[0]
    assert "hot magenta" in hook_motion_prompts[0]
    assert all("intense cyan" in prompt for prompt in hook_still_prompts)
    assert all("hot magenta" in prompt for prompt in hook_still_prompts)


def test_generate_video_grades_body_stills_before_moviepy_animation():
    source = inspect.getsource(video_generator.generate_video)

    assert "apply_color_intensity_to_images(" in source
    assert "color_intensity=color_intensity" in source
    assert "color_intensity=color_intensity," in source


def test_scene_and_legacy_plans_never_exceed_shot_cap_and_preserve_time():
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
    legacy_plan = video_generator.build_legacy_shot_plan(["one short beat"], 9.1)

    for plan, expected_total in ((scene_plan, 11.7), (legacy_plan, 9.1)):
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


def test_premium_and_body_frames_use_distinct_fal_models(monkeypatch):
    calls = {}

    def generate(prompt, **kwargs):
        calls[prompt] = kwargs
        return Image.new("RGB", (2, 2))

    monkeypatch.setattr(video_generator, "generate_image_fal", generate)

    video_generator._parallel_image_gen(
        ["opening", "body"],
        premium_flags=[True, False],
    )

    assert calls["opening"]["model"] == video_generator.FAL_HOOK_IMAGE_MODEL
    assert calls["opening"]["num_inference_steps"] is None
    assert calls["body"]["model"] == video_generator.FAL_IMAGE_MODEL
    assert (
        calls["body"]["num_inference_steps"]
        == video_generator.FAL_IMAGE_STEPS
    )


def test_mixed_real_failure_falls_back_to_correct_premium_and_body_models(
    monkeypatch,
):
    shots = [
        {
            "speech": "Premium opening",
            "visual": "a distant planet",
            "_scene_index": 0,
            "_shot_type": "macro close-up",
        },
        {
            "speech": "Cheap body",
            "visual": "a telescope array",
            "_scene_index": 3,
            "_shot_type": "wide establishing shot",
        },
    ]
    calls = {}
    monkeypatch.setattr(
        visual_styles,
        "apply_style",
        lambda visual, *_args, **_kwargs: visual,
    )
    monkeypatch.setattr(
        real_imagery,
        "fetch_nasa_image",
        lambda *_args, **_kwargs: None,
    )

    def generate(prompt, **kwargs):
        calls[prompt] = kwargs
        return Image.new("RGB", (2, 2))

    monkeypatch.setattr(video_generator, "generate_image_fal", generate)

    video_generator.generate_scene_images(
        shots,
        "cinematic",
        image_source="mixed",
        series_lane="space",
    )

    premium_prompt = next(
        prompt
        for prompt in calls
        if prompt.startswith("a distant planet, macro close-up")
    )
    body_prompt = next(
        prompt
        for prompt in calls
        if prompt.startswith("a telescope array, wide establishing shot")
    )
    assert calls[premium_prompt]["model"] == (
        video_generator.FAL_HOOK_IMAGE_MODEL
    )
    assert calls[premium_prompt]["num_inference_steps"] is None
    assert calls[body_prompt]["model"] == (
        video_generator.FAL_IMAGE_MODEL
    )
    assert calls[body_prompt]["num_inference_steps"] == (
        video_generator.FAL_IMAGE_STEPS
    )


def test_series_lane_never_drives_visual_sourcing(
    monkeypatch,
):
    shots = [
        {
            "speech": f"Scene {index}",
            "visual": f"space subject {index}",
            "_scene_index": index,
            "_shot_type": video_generator.SHOT_TYPES[
                index % len(video_generator.SHOT_TYPES)
            ],
        }
        for index in range(6)
    ]
    monkeypatch.setattr(
        visual_styles,
        "apply_style",
        lambda visual, *_args, **_kwargs: visual,
    )
    monkeypatch.setattr(
        real_imagery,
        "fetch_hero_image",
        lambda *_args, **_kwargs: "hero",
    )
    monkeypatch.setattr(
        real_imagery,
        "fetch_nasa_image",
        lambda query, **_kwargs: f"nasa:{query}",
    )
    monkeypatch.setattr(
        video_generator,
        "generate_image_fal",
        lambda prompt, **_kwargs: f"ai:{prompt}",
    )

    images = video_generator.generate_scene_images(
        shots,
        "cinematic",
        image_source="mixed",
        series_lane="space",
        hero_image="https://example.test/hero.jpg",
    )

    assert images[0] == "hero"
    assert images[0] == "hero"
    assert all(images[index].startswith(f"ai:space subject {index}") for index in range(1, 6))


def test_legacy_series_category_does_not_trigger_nasa_fetches(
    monkeypatch,
):
    shots = [
        {
            "speech": f"Scene {index}",
            "visual": visual,
            "_scene_index": index,
            "_shot_type": video_generator.SHOT_TYPES[
                index % len(video_generator.SHOT_TYPES)
            ],
        }
        for index, visual in enumerate(
            [
                "Mars crater",
                "AI scene one",
                "Jupiter storm",
                "AI scene three",
                "Mars crater",
                "AI scene five",
            ]
        )
    ]
    nasa_calls = []
    monkeypatch.setattr(
        visual_styles,
        "apply_style",
        lambda visual, *_args, **_kwargs: visual,
    )

    def fetch_nasa(query, **kwargs):
        nasa_calls.append((query, kwargs["preferred_index"]))
        return f"nasa:{query}:{kwargs['preferred_index']}"

    monkeypatch.setattr(real_imagery, "fetch_nasa_image", fetch_nasa)
    monkeypatch.setattr(
        video_generator,
        "generate_image_fal",
        lambda prompt, **_kwargs: f"ai:{prompt}",
    )

    video_generator.generate_scene_images(
        shots,
        "cinematic",
        image_source="mixed",
        series_lane="space",
    )

    assert nasa_calls == []


def test_scene_stock_splits_use_shot_type_and_distinct_offsets(monkeypatch):
    shots = [
        {
            "speech": "One long scene",
            "visual": "Jupiter storm",
            "_shot_type": shot_type,
        }
        for shot_type in video_generator.SHOT_TYPES
    ]
    calls = []

    def search(query, count):
        calls.append((query, count))
        return [f"{query}:{index}" for index in range(count)]

    monkeypatch.setattr(video_generator, "search_pexels_images", search)

    images = video_generator.generate_scene_images(
        shots,
        "cinematic",
        image_source="stock",
    )

    assert [count for _query, count in calls] == [1, 2, 3, 4]
    assert all(
        shot_type in calls[index][0]
        for index, shot_type in enumerate(video_generator.SHOT_TYPES)
    )
    assert len(set(images)) == len(images)


def test_legacy_mixed_mode_uses_shot_aligned_real_imagery_path():
    source = inspect.getsource(video_generator.generate_video)

    assert 'elif image_source == "mixed":' in source
    assert "generate_scene_images(" in source
    assert "series_lane=series_lane" in source
    assert "hero_image=hero_image" in source


def test_real_image_request_retries_once_with_timeout(monkeypatch):
    calls = []

    class _Response:
        url = "https://images-api.nasa.gov/search"

        def raise_for_status(self):
            return None

    def request(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            raise TimeoutError("first request stalled")
        return _Response()

    monkeypatch.setattr(
        real_imagery,
        "_validate_public_url",
        lambda url, **_kwargs: url,
    )
    monkeypatch.setattr(real_imagery.requests, "get", request)
    monkeypatch.setattr(real_imagery.time, "sleep", lambda _seconds: None)

    response = real_imagery._request_with_retry(
        "https://images-api.nasa.gov/search"
    )

    assert isinstance(response, _Response)
    assert len(calls) == 2
    assert all(call[1]["timeout"] == real_imagery.REQUEST_TIMEOUT_SECONDS for call in calls)


def test_entire_requests_get_honors_deadline_and_closes_late_response(
    monkeypatch,
):
    release = threading.Event()
    response_closed = threading.Event()
    calls = []

    class _LateResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def close(self):
            response_closed.set()

    late_response = _LateResponse()

    def stalled_request(url, **kwargs):
        calls.append((url, kwargs))
        release.wait(2)
        return late_response

    monkeypatch.setattr(
        real_imagery,
        "_validate_public_url",
        lambda url, **_kwargs: url,
    )
    monkeypatch.setattr(real_imagery.requests, "get", stalled_request)

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="deadline exceeded"):
            real_imagery._request_with_retry(
                "https://public.example/image.jpg",
                deadline=time.monotonic() + 0.05,
            )
    finally:
        release.set()

    assert len(calls) == 1
    assert time.monotonic() - started < 0.5
    assert response_closed.wait(0.5)


def test_real_image_redirect_is_validated_before_private_target_fetch(monkeypatch):
    requested_urls = []

    class _Redirect:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self):
            return None

        def raise_for_status(self):
            return None

    def validate(url, **_kwargs):
        if "127.0.0.1" in url:
            raise ValueError("private target")
        return url

    def request(url, **_kwargs):
        requested_urls.append(url)
        return _Redirect()

    monkeypatch.setattr(real_imagery, "_validate_public_url", validate)
    monkeypatch.setattr(real_imagery.requests, "get", request)
    monkeypatch.setattr(real_imagery.time, "sleep", lambda _seconds: None)

    with pytest.raises(ValueError, match="private target"):
        real_imagery._request_with_retry("https://public.example/image")

    assert requested_urls == [
        "https://public.example/image",
        "https://public.example/image",
    ]


def test_public_url_dns_resolution_honors_absolute_deadline(monkeypatch):
    release = threading.Event()

    def stalled_resolution(*_args, **_kwargs):
        release.wait(2)
        return [
            (
                real_imagery.socket.AF_INET,
                real_imagery.socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]

    monkeypatch.setattr(
        real_imagery.socket,
        "getaddrinfo",
        stalled_resolution,
    )

    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="DNS resolution timed out"):
            real_imagery._validate_public_url(
                "https://public.example/image.jpg",
                deadline=time.monotonic() + 0.05,
            )
    finally:
        release.set()

    assert time.monotonic() - started < 0.5


def test_real_image_stream_stops_at_byte_limit():
    class _LargeResponse:
        headers = {}
        closed = False

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield b"a" * 8
            yield b"b" * 8

        def close(self):
            self.closed = True

    response = _LargeResponse()
    with pytest.raises(ValueError, match="byte limit"):
        real_imagery._read_limited_bytes(response, 12)

    assert response.closed


def test_real_image_stream_stops_when_deadline_expires_mid_body():
    release = threading.Event()

    class _SlowResponse:
        headers = {}
        closed = False
        chunks_started = 0

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            self.chunks_started += 1
            yield b"first"
            release.wait(2)
            yield b"second"

        def close(self):
            self.closed = True

    response = _SlowResponse()
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="body deadline exceeded"):
            real_imagery._read_limited_bytes(
                response,
                1024,
                deadline=time.monotonic() + 0.1,
            )
    finally:
        release.set()

    assert response.chunks_started == 1
    assert response.closed
    assert time.monotonic() - started < 0.75


def test_nasa_collection_array_prefers_original_asset(monkeypatch):
    manifest = [
        "http://images-assets.nasa.gov/image/PIA21376/PIA21376~orig.jpg",
        "http://images-assets.nasa.gov/image/PIA21376/PIA21376~small.jpg",
        "http://images-assets.nasa.gov/image/PIA21376/PIA21376~thumb.jpg",
        "http://images-assets.nasa.gov/image/PIA21376/metadata.json",
    ]

    class _ManifestResponse:
        headers = {}

        def iter_content(self, chunk_size):
            assert chunk_size == 64 * 1024
            yield json.dumps(manifest).encode("utf-8")

        def close(self):
            return None

    monkeypatch.setattr(
        real_imagery,
        "_request_with_retry",
        lambda *_args, **_kwargs: _ManifestResponse(),
    )

    urls = real_imagery._manifest_urls(
        {
            "href": (
                "https://images-assets.nasa.gov/image/PIA21376/"
                "collection.json"
            ),
            "links": [],
        },
        deadline=float("inf"),
    )

    assert urls[0].endswith("PIA21376~orig.jpg")
    assert all(not url.endswith("metadata.json") for url in urls)


def test_motion_hook_is_split_into_capped_edit_shots():
    class _Segment:
        def __init__(self, duration):
            self.duration = duration

    class _Clip:
        def subclip(self, start, end):
            return _Segment(end - start)

    clip = _Clip()
    segments = video_generator.split_motion_clip(clip, 5.0)

    assert len(segments) == 2
    assert max(segment.duration for segment in segments) <= (
        video_generator.MAX_SHOT_DURATION
    )
    assert segments[0]._scap_parent_clip is clip


def test_still_cost_is_floorless_count_math():
    assert video_generator.estimate_ai_still_cost(8, 16) == pytest.approx(
        (8 * 0.025) + (16 * 0.003)
    )
    assert math.isclose(
        video_generator.estimate_ai_still_cost(-2, -3),
        0.0,
    )


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

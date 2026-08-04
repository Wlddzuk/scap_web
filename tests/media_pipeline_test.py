"""Fast unit coverage for bounded motion and narration-safe music helpers."""

from pathlib import Path
import wave

import numpy as np
from moviepy.audio.AudioClip import AudioClip
import pytest

import video_generator


def sample_scenes():
    return [
        {"visual": "opening telescope", "speech": "Look at this.", "emotion": "curiosity"},
        {"visual": "quiet laboratory", "speech": "Researchers measured it.", "emotion": "neutral"},
        {"visual": "star exploding", "speech": "Then everything changed!", "emotion": "awe"},
        {"visual": "new planet", "speech": "A world appeared.", "emotion": "surprise"},
    ]


def test_motion_scene_selection_respects_limit_and_skips_hook_scene():
    selected = video_generator.select_motion_scene_indexes(sample_scenes(), 2)

    assert len(selected) == 2
    assert 0 not in selected
    assert 2 in selected


def test_failed_body_motion_generation_returns_still_fallbacks(monkeypatch):
    monkeypatch.setattr(
        video_generator,
        "generate_motion_video_fal",
        lambda *args, **kwargs: None,
    )

    clips = video_generator.create_body_motion_clips(
        sample_scenes(),
        durations=[1.0, 1.0, 1.0, 1.0],
        style_key=None,
        video_model="test-model",
        clip_limit=2,
    )

    assert clips == {}


def test_music_envelope_ducks_during_speech():
    gap_gain = video_generator._music_gain_for_time(0.1, [(0.4, 0.8)])
    speech_gain = video_generator._music_gain_for_time(0.5, [(0.4, 0.8)])

    assert speech_gain < gap_gain
    assert round(speech_gain, 4) == round(10 ** (-22 / 20), 4)
    assert round(gap_gain, 4) == round(10 ** (-12 / 20), 4)


def test_fal_still_generation_has_bounded_wait_and_fallback(monkeypatch):
    import fal_client

    call = {}

    def fail_run(*args, **kwargs):
        call.update(kwargs)
        raise TimeoutError("provider stalled")

    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(fal_client, "run", fail_run)
    monkeypatch.setattr(video_generator.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(video_generator, "create_gradient_background", lambda: "fallback")

    result = video_generator.generate_image_fal("a telescope", retry_count=1)

    assert result == "fallback"
    assert call["timeout"] == video_generator.FAL_IMAGE_TIMEOUT_SECONDS
    assert call["start_timeout"] <= call["timeout"]


def test_motion_clip_cap_enforces_count_and_dollar_ceiling():
    # Reused Illustrated Science frames leave room for two optional $0.18
    # motion requests under the same $0.60 ceiling.
    assert video_generator._effective_motion_clip_cap(12, 0.18) == 2
    assert video_generator._effective_motion_clip_cap(12, 0.25) == 1
    assert video_generator._effective_motion_clip_cap(2, 0.18) == 2
    # The helper still respects an explicitly cheaper base configuration.
    assert (
        video_generator._effective_motion_clip_cap(
            12,
            0.18,
            base_cost=0.05,
        )
        == 3
    )


def test_planned_scene_still_cost_uses_actual_premium_and_cheap_shots():
    body_shots = [
        {"_scene_index": 0},
        {"_scene_index": 0},
        {"_scene_index": 1},
        {"_scene_index": 2},
        {"_scene_index": 3},
    ]

    planned = video_generator.estimate_planned_still_cost(
        body_shots,
        use_scenes=True,
        image_source="mixed",
    )

    assert planned == pytest.approx(
        video_generator.estimate_ai_still_cost(4, 0)
    )


def test_planned_legacy_and_stock_still_costs_use_safe_fixed_rules():
    oversized_legacy_plan = [{} for _ in range(40)]

    assert video_generator.estimate_planned_still_cost(
        oversized_legacy_plan,
        use_scenes=False,
        image_source="ai",
    ) == pytest.approx(
        video_generator.estimate_ai_still_cost(
            video_generator.NUM_HOOK_IMAGES,
            video_generator.NUM_BODY_IMAGES,
        )
    )
    assert video_generator.estimate_planned_still_cost(
        oversized_legacy_plan,
        use_scenes=False,
        image_source="stock",
    ) == 0.0


def test_render_disables_motion_when_its_planned_stills_exhaust_budget(
    monkeypatch,
    tmp_path,
):
    class _Audio:
        duration = 60.0

        def close(self):
            return None

    captured = {}
    expensive_plan = [
        {"_scene_index": 2, "_duration": 1.0}
        for _ in range(200)
    ]

    def stop_at_hook(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after motion budget decision")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    monkeypatch.setattr(
        video_generator.tts_engine,
        "synthesize",
        lambda *_args, **_kwargs: "voice.wav",
    )
    monkeypatch.setattr(
        video_generator,
        "AudioFileClip",
        lambda _path: _Audio(),
    )
    monkeypatch.setattr(
        video_generator,
        "build_scene_shot_plan",
        lambda *_args, **_kwargs: expensive_plan,
    )
    monkeypatch.setattr(
        video_generator,
        "generate_referent_scene_images",
        lambda *_args, **_kwargs: [object()] * len(expensive_plan),
    )
    monkeypatch.setattr(
        video_generator,
        "apply_color_intensity_to_images",
        lambda images, *_args, **_kwargs: images,
    )
    monkeypatch.setattr(
        video_generator,
        "create_hook_clips",
        stop_at_hook,
    )

    with pytest.raises(
        RuntimeError,
        match="stop after motion budget decision",
    ):
        video_generator.generate_video(
            article_id=17,
            title="Synthetic budget test",
            script="A deliberately long visual plan.",
            image_source="ai",
            captions=False,
            scenes=[{"visual": "laboratory", "speech": "A result."}],
            style_key="editorial",
            use_video_hook=True,
        )

    assert captured["use_video_hook"] is False


def test_music_mix_normalizes_track_before_target_gain(tmp_path):
    sample_rate = 44_100
    seconds = 1
    times = np.arange(sample_rate * seconds) / sample_rate
    samples = (0.08 * np.sin(2 * np.pi * 220 * times) * 32767).astype("<i2")
    track_path = tmp_path / "quiet.wav"
    with wave.open(str(track_path), "wb") as track:
        track.setnchannels(1)
        track.setsampwidth(2)
        track.setframerate(sample_rate)
        track.writeframes(samples.tobytes())

    def silence(frame_time):
        if np.ndim(frame_time) == 0:
            return np.zeros(1)
        return np.zeros((len(frame_time), 1))

    narration = AudioClip(silence, duration=4.0, fps=sample_rate)
    mixed, resources = video_generator.create_music_mix(
        narration,
        timed_words=[{"start": 2.0, "end": 2.4}],
        duration=4.0,
        article_id=0,
        music_dir=tmp_path,
    )
    try:
        gap_times = np.linspace(1.0, 1.2, 5000, endpoint=False)
        speech_times = np.linspace(2.1, 2.3, 5000, endpoint=False)
        gap_peak = float(np.max(np.abs(mixed.get_frame(gap_times))))
        speech_peak = float(np.max(np.abs(mixed.get_frame(speech_times))))

        assert gap_peak == pytest.approx(10 ** (-12 / 20), rel=0.02)
        assert speech_peak == pytest.approx(10 ** (-22 / 20), rel=0.02)
    finally:
        for resource in reversed(resources):
            resource.close()
        narration.close()


class _FakeAudio:
    duration = 2.0

    def close(self):
        return None


class _FailingRenderClip:
    duration = 2.0

    def set_audio(self, _audio):
        return self

    def write_videofile(self, output_path, **_kwargs):
        Path(output_path).write_bytes(b"partial")
        raise RuntimeError("ffmpeg failed")

    def close(self):
        return None


def test_failed_render_removes_partial_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MUSIC_ENABLED", "false")
    monkeypatch.setattr(video_generator.tts_engine, "synthesize", lambda *_args, **_kwargs: "voice.wav")
    monkeypatch.setattr(video_generator, "AudioFileClip", lambda _path: _FakeAudio())
    monkeypatch.setattr(video_generator, "generate_themed_images", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(video_generator, "create_hook_clips", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(video_generator, "create_clip", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(video_generator, "concatenate_videoclips", lambda *_args, **_kwargs: _FailingRenderClip())
    monkeypatch.setattr(video_generator, "create_headline_clip", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        video_generator.generate_video(
            article_id=9,
            title="Test",
            script="short script",
            image_source="ai",
            captions=False,
            use_video_hook=False,
        )

    assert list((tmp_path / "static" / "videos").glob("article_9_*.mp4")) == []

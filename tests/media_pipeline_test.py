"""Fast unit coverage for image retries, narration-safe music and failed renders."""

from pathlib import Path
import wave

import numpy as np
from moviepy.audio.AudioClip import AudioClip
import pytest

import video_generator


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
    monkeypatch.setattr(video_generator, "transcribe_word_timestamps", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("pixel_scenes.generate_scene_images", lambda shots, **_kwargs: [object()] * len(shots))
    monkeypatch.setattr(video_generator, "create_hook_clips", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr("moss_sprite.create_moss_overlay", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(video_generator, "create_clip", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(video_generator, "concatenate_videoclips", lambda *_args, **_kwargs: _FailingRenderClip())
    monkeypatch.setattr(video_generator, "create_headline_clip", lambda *_args, **_kwargs: None)

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        video_generator.generate_video(
            article_id=9,
            title="Test",
            script="short script",
            captions=False,
        )

    assert list((tmp_path / "static" / "videos").glob("article_9_*.mp4")) == []

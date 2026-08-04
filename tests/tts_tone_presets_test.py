"""Unit coverage for Clipper's narration tone presets."""

from types import SimpleNamespace

import numpy as np
import pytest

import tts_engine
import video_generator


def test_controlled_is_the_default_curiosity_preset():
    assert tts_engine.DEFAULT_VOICE_TONE == "controlled"
    assert tts_engine.normalize_voice_tone(None) == "controlled"
    assert tts_engine.normalize_voice_tone("not-a-preset") == "controlled"

    preset = tts_engine.VOICE_TONE_PRESETS["controlled"]
    assert preset["concept"] == "Curious Energy"
    assert preset["gemini_voice"] == "Achird"
    assert preset["kokoro_speed"] == pytest.approx(1.05)


def test_presets_have_distinct_voice_and_delivery_profiles():
    assert tts_engine.gemini_voice_for_tone("controlled") == "Achird"
    assert tts_engine.gemini_voice_for_tone("energetic") == "Puck"
    assert tts_engine.gemini_voice_for_tone("documentary") == "Charon"

    assert tts_engine.pick_voice_for_tone("controlled") == ("af_heart", 1.05)
    assert tts_engine.pick_voice_for_tone("energetic") == ("af_bella", 1.10)
    assert tts_engine.pick_voice_for_tone("documentary") == (
        "am_michael",
        1.00,
    )

    controlled = tts_engine.style_instruction_for_tone("controlled")
    assert "opening sentence with strong curiosity" in controlled
    assert "natural conversational rhythm" in controlled
    assert "Raise the energy for the main reveal" in controlled
    assert "Never shout" in controlled
    assert "breathless sales cadence" in controlled


def test_gemini_controlled_preset_sets_prompt_and_natural_voice(
    monkeypatch,
    tmp_path,
):
    from google import genai

    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                output_audio=SimpleNamespace(data=b"\x00\x00")
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.interactions = FakeInteractions()

        def close(self):
            captured["closed"] = True

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_TTS_VOICE", raising=False)
    monkeypatch.setattr(genai, "Client", FakeClient)

    output = tts_engine._synthesize_gemini_once(
        "Octopuses solve this problem together.",
        str(tmp_path / "voice.wav"),
        voice=None,
        emotion="shocking",
        voice_tone="controlled",
    )

    assert output.endswith("voice.wav")
    assert captured["generation_config"]["speech_config"] == [
        {"voice": "Achird"}
    ]
    assert captured["input"].startswith(
        tts_engine.style_instruction_for_tone("controlled")
    )
    assert "genuinely stunned" not in captured["input"]
    assert captured["closed"] is True


def test_kokoro_tone_controls_voice_and_rate_not_visual_emotion(
    monkeypatch,
    tmp_path,
):
    captured = {}

    def fake_pipeline(text, *, voice, speed):
        captured.update(text=text, voice=voice, speed=speed)
        return [(None, None, np.zeros(20, dtype=np.float32))]

    monkeypatch.delenv("KOKORO_VOICE", raising=False)
    monkeypatch.setattr(tts_engine, "_get_kokoro_pipeline", lambda: fake_pipeline)

    tts_engine._synthesize_kokoro_once(
        "The reveal is specific.",
        str(tmp_path / "voice.wav"),
        voice=None,
        emotion="shocking",
        voice_tone="controlled",
    )

    assert captured["voice"] == "af_heart"
    assert captured["speed"] == pytest.approx(1.05)


def test_explicit_voice_environment_override_still_wins(monkeypatch):
    monkeypatch.setenv("GEMINI_TTS_VOICE", "Aoede")
    monkeypatch.setenv("KOKORO_VOICE", "bf_emma")

    assert tts_engine.default_voice_for_engine("gemini", "documentary") == "Aoede"
    assert tts_engine.default_voice_for_engine("kokoro", "energetic") == "bf_emma"


def test_tone_survives_gemini_to_kokoro_fallback(monkeypatch):
    calls = []

    def fake_engine(engine, text, output_path, voice, emotion, voice_tone):
        calls.append((engine, voice_tone))
        if engine == "gemini":
            raise RuntimeError("provider unavailable")
        return output_path

    monkeypatch.setenv("TTS_ENGINE", "auto")
    monkeypatch.setattr(tts_engine, "synthesize_with_engine", fake_engine)

    result = tts_engine.synthesize(
        "A clear science fact.",
        "voice.wav",
        voice_tone="documentary",
    )

    assert result == "voice.wav"
    assert calls == [
        ("gemini", "documentary"),
        ("kokoro", "documentary"),
    ]


def test_generate_video_passes_voice_tone_to_synthesis(monkeypatch, tmp_path):
    captured = {}

    def stop_after_tts(*args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after tts")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(video_generator.tts_engine, "synthesize", stop_after_tts)

    with pytest.raises(RuntimeError, match="stop after tts"):
        video_generator.generate_video(
            article_id=4,
            title="Test",
            script="A direct, concrete science story.",
            captions=False,
            voice_tone="documentary",
        )

    assert captured["emotion"] is None
    assert captured["voice_tone"] == "documentary"

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

    # The hook cuts only within scene 1, then the body resumes at the shot on
    # screen when the hook ends, in plan order, so no later scene plays twice.
    hook_len = vg.hook_duration(full_plan, 12.0)
    cuts = max(1, min(vg.NUM_HOOK_IMAGES, int(hook_len / vg.MIN_HOOK_CUT)))
    scene_one = {id(images[slot]) for slot, shot in enumerate(full_plan) if shot["_scene_index"] == 0}
    hook = created_images[:cuts]
    assert hook[0] is images[0]
    assert {id(image) for image in hook} <= scene_one
    body = created_images[cuts:]
    body_slots = vg.split_plan_at_hook(full_plan, hook_len)
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


def test_hook_lasts_as_long_as_scene_one_is_spoken():
    scenes = [
        {"speech": "One workout kept muscle.", "visual": "a"},
        {"speech": "Researchers tested three intensities.", "visual": "b"},
    ]
    timed_words = _timed([
        ("One", 0.1), ("workout", 0.4), ("kept", 0.8), ("muscle.", 1.2),
        ("Researchers", 2.8), ("tested", 3.3), ("three", 3.7), ("intensities.", 4.0),
    ])
    plan = vg.build_scene_shot_plan(scenes, 6.0, timed_words)

    assert vg.hook_duration(plan, 6.0) == pytest.approx(2.8)
    assert vg.split_plan_at_hook(plan, 2.8)[0][1]["_scene_index"] == 1
    # A very long or very short first scene is clamped.
    assert vg.hook_duration([{"_duration": 9.0, "_scene_index": 0}], 20.0) == vg.HOOK_DURATION
    assert vg.hook_duration([{"_duration": 0.4, "_scene_index": 0}], 20.0) == vg.MIN_HOOK_DURATION


def test_alignment_drops_the_script_echoed_ahead_of_the_narration():
    # faster-whisper sometimes replays the end of its initial prompt (the
    # script) before the first real word, which put "WOULD YOU" over second one.
    script = "Only one workout keeps muscle. Would you trade steady cardio for sprints?"
    echo = ["Would", "you", "trade", "steady", "cardio"]
    heard = [{"text": t, "start": 0.05 * i, "end": 0.05 * i + 0.04} for i, t in enumerate(echo)]
    spoken = script.split()
    heard += [{"text": t, "start": 0.5 + 0.4 * i, "end": 0.8 + 0.4 * i} for i, t in enumerate(spoken)]

    aligned = vg.align_words_to_script(heard, script)

    assert [w["text"] for w in aligned] == spoken
    assert aligned[0]["start"] == pytest.approx(0.5)


def test_transcription_skips_the_script_prompt_and_falls_back_to_it(monkeypatch):
    script = " ".join(f"word{i}" for i in range(20))
    prompts = []

    def fake_model(no_prompt_count):
        class FakeModel:
            def transcribe(self, _path, initial_prompt=None, **_kwargs):
                prompts.append(initial_prompt)
                count = 20 if initial_prompt else no_prompt_count
                words = [SimpleNamespace(word=f"word{i}", start=i * 0.3, end=i * 0.3 + 0.2) for i in range(count)]
                return [SimpleNamespace(words=words)], None
        return FakeModel()

    monkeypatch.setattr(vg, "_get_whisper_model", lambda _name: fake_model(20))
    assert len(vg.transcribe_word_timestamps("voice.wav", script_text=script)) == 20
    assert prompts == [None]

    prompts.clear()
    monkeypatch.setattr(vg, "_get_whisper_model", lambda _name: fake_model(5))
    assert len(vg.transcribe_word_timestamps("voice.wav", script_text=script)) == 20
    assert prompts == [None, script]

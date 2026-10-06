"""Narration prompt and anti-slop quality-gate regression tests."""

import json
from types import SimpleNamespace

import pytest

import summarizer
from summarizer import (
    find_longform_quality_issues,
    find_script_quality_issues,
    find_summary_contract_issues,
    get_prompt,
)


def _summary(hook, target_words=115):
    cta_question = "What should researchers test next?"
    filler_source = (
        "Researchers documented the behavior across repeated field observations "
        "and compared every encounter with earlier records from the same "
        "population. Their measurements tracked timing movement distance and "
        "the response of nearby animals while independent teams reviewed the "
        "evidence. The repeated pattern gave the group a concrete way to test "
        "the explanation against future observations under similar conditions"
    ).split()
    reserved_words = len(hook.split()) + len(cta_question.split())
    filler_count = target_words - reserved_words
    assert filler_count > 0
    filler_words = [
        filler_source[index % len(filler_source)]
        for index in range(filler_count)
    ]
    body = " ".join(filler_words) + "."
    script_with_cta = f"{hook} {body} {cta_question}"
    return {
        "tldr": "A concise source summary.",
        "bullets": ["One supported fact."],
        "video_script": script_with_cta,
        "hashtags": ["#Orcas", "#AnimalBehavior", "#Science"],
        "cover_line": "ORCAS HUNT TOGETHER",
        "cta_question": cta_question,
        "search_caption": "How orcas coordinate while hunting whale sharks.",
        "series_lane": "other",
        "scenes": [
            {
                "speech": hook,
                "visual": "a researcher measures a sample inside a laboratory",
                "emotion": "curious",
            },
            {
                "speech": f"{body} {cta_question}",
                "visual": "a field team compares measurements beside the animals",
                "emotion": "curious",
            }
        ],
        "hook_variants": [
            hook,
            "The evidence changes where researchers look next.",
            "One repeated pattern could explain the whole result.",
        ],
        "best_hook_index": 0,
        "dominant_emotion": "curious",
        "suggested_style": "documentary",
    }


def test_prompt_uses_curious_energy_without_old_hype_instructions():
    prompt = get_prompt("Test title", "A measured source article.")

    assert "CURIOUS ENERGY" in prompt
    assert "natural and conversational" in prompt
    assert "slightly more energized for the main reveal" in prompt
    assert "complete spoken sentences with varied rhythm" in prompt
    assert "MrBeast" not in prompt
    assert "Every line should be tweetable" not in prompt
    assert 'Make it personal with "you"/"your"' not in prompt
    assert "Mic-drop line" not in prompt
    assert "Name the study, institution, researcher" in prompt
    assert "Do not use throat-clearing" in prompt
    assert "Use colons for real lists" in prompt
    assert "Do not recap the script" in prompt
    assert "SINGLE-FACT MANDATE" in prompt
    assert "CONSEQUENCE-FIRST HOOK" in prompt
    assert "100-130 words" in prompt
    assert "12 words or fewer" in prompt
    assert "Curiosity gaps and genuine questions are allowed" in prompt
    assert '"cover_line"' in prompt
    assert '"cta_question"' in prompt
    assert '"search_caption"' in prompt
    assert '"series_lane"' in prompt
    assert "exactly 3 high-relevance hashtags" in prompt
    assert "five key points" not in prompt


def test_gemini_summarizer_uses_a_non_versioned_model_alias_by_default(monkeypatch):
    monkeypatch.setenv("GEMINI_SUMMARIZER_MODEL", "")
    assert summarizer._gemini_summarizer_model_name() == "gemini-flash-latest"

    monkeypatch.setenv("GEMINI_SUMMARIZER_MODEL", "gemini-3.6-flash")
    assert summarizer._gemini_summarizer_model_name() == "gemini-3.6-flash"


def test_follow_invitation_preserves_final_question_and_scene_contract():
    result = _summary("Orcas coordinate their hunts.")
    invitation = "Follow for the evidence behind animal behavior."
    question = result["cta_question"]
    final_speech = result["scenes"][-1]["speech"]
    result["scenes"][-1]["speech"] = final_speech.removesuffix(question) + (
        f"{invitation} {question}"
    )
    result["video_script"] = " ".join(
        scene["speech"] for scene in result["scenes"]
    )

    parsed = summarizer.parse_response(json.dumps(result))

    assert invitation in parsed["video_script"]
    assert parsed["video_script"].endswith(question)
    assert parsed["scenes"][-1]["speech"].endswith(question)
    assert find_summary_contract_issues(parsed) == []
    assert find_script_quality_issues(parsed["video_script"]) == []


@pytest.mark.parametrize(
    ("script", "expected_issue"),
    [
        (
            "You've been lied to about these animals.",
            "clickbait phrase",
        ),
        (
            "Researchers tracked the whale — then measured its speed.",
            "em/en dash",
        ),
        (
            "Could this work? Why did it happen? What should researchers test next?",
            "excessive questions",
        ),
        (
            "This is incredible! The result is insane! Researchers checked it.",
            "excessive exclamation marks",
        ),
        (
            "This is not just a hunting technique, it is weaponized physics.",
            "binary contrast formula",
        ),
        (
            "The groundbreaking result is a game-changing discovery.",
            "generic hype",
        ),
        (
            "Belly up. Full speed. Impact. The second whale waits nearby.",
            "stacked sentence fragments",
        ),
        (
            "Here's the thing: the sample changed overnight.",
            "throat-clearing opener",
        ),
        (
            "What nobody tells you is that the second sensor failed.",
            "faux-insight setup",
        ),
        (
            "The detail: the sample warmed overnight.",
            "colon reveal",
        ),
        (
            "The launch added two sensors, highlighting the team's commitment.",
            "superficial -ing analysis",
        ),
        (
            "The result marks a pivotal moment for marine science.",
            "importance puffery",
        ),
        (
            "Experts agree the behavior proves intelligence.",
            "vague attribution",
        ),
        (
            "The dashboard serves as a centralized hub for every result.",
            "fake-strong verb",
        ),
        (
            "Not a tool. Not a toy. A revolution.",
            "negative listing",
        ),
        (
            "Scientists delve into a tapestry of biological signals.",
            "inflated vocabulary",
        ),
        (
            "The team will test the sensor again. Ultimately, nature always finds a way.",
            "summary-recap ending",
        ),
    ],
)
def test_quality_checker_catches_high_signal_slop(script, expected_issue):
    issues = find_script_quality_issues(script)

    assert any(expected_issue in issue for issue in issues)


def test_quality_checker_accepts_specific_conversational_narration():
    script = (
        "Two orcas approach the whale shark from opposite sides. "
        "One turns the fish while the other accelerates toward its exposed belly. "
        "Researchers recorded the same coordinated move across several hunts. "
        "The pattern suggests that adults may teach the technique to younger whales."
    )

    assert find_script_quality_issues(script) == []


@pytest.mark.parametrize(
    "script",
    [
        "They tested three temperatures: 5, 15, and 25 degrees.",
        "A 2025 Oxford study tracked 74 nests across two breeding seasons.",
        "The probe crossed the plume, collecting ice grains for analysis.",
        "The moon rose. Temperatures fell. Ice formed.",
        (
            "The next expedition leaves in October. "
            "It will test whether the signal survives an Antarctic winter."
        ),
        (
            "The team used a robust regression model because three sensors failed. "
            "That choice kept the damaged readings from dominating the result."
        ),
    ],
)
def test_quality_checker_preserves_legitimate_science_language(script):
    assert find_script_quality_issues(script) == []


def test_quality_gate_tries_next_provider_after_sloppy_result(monkeypatch):
    sloppy = _summary(
        "You've been lied to! Belly up. Full speed. Impact. This changes everything!"
    )
    clean = _summary(
        "Orcas coordinate their turns before one animal accelerates toward the fish."
    )
    calls = []

    def kimi(_title, _content):
        calls.append("kimi")
        return sloppy

    def qwen(_title, _content):
        calls.append("qwen")
        return clean

    monkeypatch.setattr(summarizer, "summarize_with_kimi", kimi)
    monkeypatch.setattr(summarizer, "summarize_with_qwen", qwen)
    monkeypatch.setattr(
        summarizer,
        "summarize_with_groq",
        lambda *_args: pytest.fail("clean Claude result should end fallback"),
    )
    monkeypatch.setattr(
        summarizer,
        "summarize_with_gemini",
        lambda *_args: pytest.fail("clean Claude result should end fallback"),
    )

    result = summarizer.summarize_article("Orca research", "Source facts")

    assert calls == ["kimi", "qwen"]
    assert result is clean


@pytest.mark.parametrize("word_count", [99, 131])
def test_summary_contract_rejects_scripts_outside_target_duration(word_count):
    result = _summary(
        "This behavior changes how researchers interpret the hunt.",
        target_words=word_count,
    )

    issues = find_summary_contract_issues(result)

    assert any(
        issue.startswith("video_script must contain 100-130 words")
        for issue in issues
    )


def test_contract_gate_tries_next_provider_after_short_script(monkeypatch):
    short = _summary(
        "This behavior changes how researchers interpret the hunt.",
        target_words=70,
    )
    complete = _summary(
        "This behavior changes how researchers interpret the hunt.",
        target_words=115,
    )
    calls = []

    def kimi(_title, _content):
        calls.append("kimi")
        return short

    def qwen(_title, _content):
        calls.append("qwen")
        return complete

    monkeypatch.setattr(summarizer, "summarize_with_kimi", kimi)
    monkeypatch.setattr(summarizer, "summarize_with_qwen", qwen)
    monkeypatch.setattr(
        summarizer,
        "summarize_with_groq",
        lambda *_args: pytest.fail("complete Claude result should end fallback"),
    )
    monkeypatch.setattr(
        summarizer,
        "summarize_with_gemini",
        lambda *_args: pytest.fail("complete Claude result should end fallback"),
    )

    result = summarizer.summarize_article("Orca research", "Source facts")

    assert calls == ["kimi", "qwen"]
    assert result is complete


@pytest.mark.parametrize(
    "hook_variants",
    [
        [],
        ["One.", "Two.", "Three.", "Four."],
        ["First hook.", "   ", "Third hook."],
        ["First hook.", 42, "Third hook."],
        "First hook.",
    ],
)
def test_summary_contract_requires_three_nonempty_hook_strings(hook_variants):
    result = _summary("This behavior changes how researchers read the hunt.")
    result["hook_variants"] = hook_variants

    issues = find_summary_contract_issues(result)

    assert "hook_variants must contain 1-3 nonempty strings" in issues


def test_summary_contract_caps_every_hook_for_the_first_three_seconds():
    long_hook = (
        "This single result changes how every researcher now searches for "
        "life elsewhere today."
    )
    result = _summary(long_hook, target_words=130)
    result["hook_variants"] = [
        long_hook,
        "This result changes where researchers look next.",
        "One pattern could explain the whole result.",
    ]

    issues = find_summary_contract_issues(result)

    assert "each hook variant must contain at most 12 words" in issues


def test_every_hook_variant_keeps_the_complete_script_in_target_range():
    result = _summary(
        "This behavior changes how researchers read the hunt.",
        target_words=104,
    )
    result["hook_variants"] = [
        result["hook_variants"][0],
        "Look again.",
        "One repeated pattern could explain the whole result.",
    ]

    issues = find_summary_contract_issues(result)

    assert (
        "every hook variant must keep video_script within 100-130 words"
        in issues
    )


def test_summary_contract_rejects_caption_metadata_that_can_break_packaging():
    result = _summary("This behavior changes how researchers read the hunt.")
    result["search_caption"] = (
        "#Orcas " + ("how these whales coordinate during a hunt " * 12)
    )
    result["hashtags"] = [
        "#" + ("Orcas" * 20),
        "#AnimalBehavior",
        "#Science",
    ]

    issues = find_summary_contract_issues(result)

    assert "search_caption must not exceed 220 characters" in issues
    assert "hashtags must be # tags no longer than 64 characters" in issues


def test_summary_contract_keeps_cta_out_of_search_caption():
    result = _summary("This behavior changes how researchers read the hunt.")
    result["search_caption"] = (
        "How orcas coordinate. What should researchers test next?"
    )

    issues = find_summary_contract_issues(result)

    assert "search_caption must not contain cta_question" in issues


@pytest.mark.parametrize("best_hook_index", [None, True, "0", 0.0, -1, 3])
def test_summary_contract_requires_strict_valid_best_hook_index(best_hook_index):
    result = _summary("This behavior changes how researchers read the hunt.")
    result["best_hook_index"] = best_hook_index

    issues = find_summary_contract_issues(result)

    assert "best_hook_index must be a strict in-range integer" in issues


def test_summary_contract_requires_best_hook_to_match_scene_and_script():
    result = _summary("This behavior changes how researchers read the hunt.")
    assert find_summary_contract_issues(result) == []

    wrong_scene = {
        **result,
        "scenes": [
            {**result["scenes"][0], "speech": "A different opening."},
            result["scenes"][1],
        ],
    }
    wrong_script = {
        **result,
        "video_script": result["video_script"].replace(
            result["hook_variants"][0],
            "Another opening changes how researchers read this hunt.",
            1,
        ),
    }

    assert (
        "best hook must exactly match scene 1 speech"
        in find_summary_contract_issues(wrong_scene)
    )
    assert (
        "best hook must exactly open video_script"
        in find_summary_contract_issues(wrong_script)
    )


def test_quality_gate_blocks_rendering_if_no_provider_clears(monkeypatch):
    noisy = _summary(
        "What if I told you this is groundbreaking? "
        "Belly up. Full speed. Impact. This changes everything!"
    )
    closest = _summary(
        "Researchers tracked the same hunting pattern — across several encounters."
    )

    monkeypatch.setattr(summarizer, "summarize_with_kimi", lambda *_args: noisy)
    monkeypatch.setattr(summarizer, "summarize_with_qwen", lambda *_args: closest)
    monkeypatch.setattr(
        summarizer,
        "summarize_with_groq",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    monkeypatch.setattr(
        summarizer,
        "summarize_with_gemini",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    with pytest.raises(Exception, match="All AI providers failed") as exc_info:
        summarizer.summarize_article("Orca research", "Source facts")

    message = str(exc_info.value)
    assert "quality gate" in message
    assert "em/en dash" in message


@pytest.mark.parametrize(
    ("draft", "expected_issue"),
    [
        (
            "## Result\n\nHere's the thing: the sample had warmed.",
            "throat-clearing opener",
        ),
        (
            "## Result\n\nStudies show the signal is reliable.",
            "vague attribution",
        ),
        (
            "## Result\n\nThe launch worked, showcasing the team's commitment.",
            "superficial -ing analysis",
        ),
        (
            "## Close\n\nOnly time will tell.",
            "fake-profound ending",
        ),
        (
            "## Close\n\nUltimately, the finding matters.",
            "summary-recap ending",
        ),
        (
            "## Result\n\nHuge.\n\n## Meaning\n\nAmazing.",
            "stacked micro-paragraphs",
        ),
    ],
)
def test_longform_quality_checker_catches_named_patterns(
    draft,
    expected_issue,
):
    issues = find_longform_quality_issues(draft)
    assert any(expected_issue in issue for issue in issues)


def test_longform_quality_checker_allows_substantive_markdown():
    draft = (
        "## What the probe measured\n\n"
        "A 2025 Oxford study tracked 74 samples at three temperatures: "
        "5, 15, and 25 degrees.\n\n"
        "## What comes next\n\n"
        "The next expedition will test whether the signal survives an "
        "Antarctic winter."
    )

    assert find_longform_quality_issues(draft) == []


def test_parse_drops_unusable_alternate_hooks_but_keeps_the_chosen_one():
    result = _summary("Orcas coordinate their hunts.")
    chosen = result["hook_variants"][result["best_hook_index"]]
    too_long = (
        "Scientists watching orcas for many years finally noticed something "
        "very strange about how they hunt"
    )
    result["hook_variants"] = [too_long, chosen, "Orcas plan their hunts together."]
    result["best_hook_index"] = 1

    parsed = summarizer.parse_response(json.dumps(result))

    assert parsed["hook_variants"] == [chosen, "Orcas plan their hunts together."]
    assert parsed["best_hook_index"] == 0
    assert find_summary_contract_issues(parsed) == []

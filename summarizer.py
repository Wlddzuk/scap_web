"""AI Summarization: Kimi K2 → Qwen Flash → Groq Llama 3.3 → Gemini Flash.

Kimi and Qwen both route through OpenRouter (one API key covers both). Groq uses
its own API for sub-second first-token latency. Gemini is the budget floor.

Produces a STORY-shaped short-form video script with:
  - Narrative arc (cold open → stakes → turn → payoff → loop)
  - Scene-by-scene visual descriptions aligned with narration beats
  - Multiple hook variants for A/B ranking
  - Style and dominant-emotion suggestions

The full narration (video_script) is fed to TTS. Scenes drive image generation.
"""

import os
import json
import re
import requests
from dotenv import load_dotenv
from visual_styles import DEFAULT_STYLE
from llm_models import GROQ_EXTRA_BODY, GROQ_TEXT_MODEL, OPENROUTER_PROVIDER_PREFS

load_dotenv()


def get_prompt(title: str, content: str) -> str:
    return f"""You write short science, culture, and current-affairs videos for a curious adult audience. Your default delivery is CURIOUS ENERGY: alert and immediate at the start, natural and conversational through the explanation, then slightly more energized for the main reveal. Attention must come from the story's concrete facts, not manufactured hype.

Turn the article below into a 60-second vertical video script. It should sound like one knowledgeable person telling another person something worth knowing.

=== SINGLE-FACT MANDATE (critical) ===
Choose the ONE most surprising, most visual fact in the article. Build the entire
script around that single fact. This is not a completeness exercise or a list of
everything worth knowing. Supporting details may appear only when they clarify,
escalate, or pay off the central fact. If a detail starts a second story, cut it.

Shape that one fact as:
1. CONSEQUENCE-FIRST HOOK (1 short complete sentence) - Lead with a concrete, source-backed consequence or surprising observation. Name the subject and what it does: "This moon sprays its ocean into space" is clearer than "This changes the search for life." Use that example only when the source supports it. Avoid vague "this discovery changes everything" promises. The hook must make sense immediately and remain scene 1.
2. SETUP - Give only the context needed to picture and understand the central fact.
3. ESCALATION - Add the strongest source-backed evidence that raises the stakes or sharpens the picture.
4. TURN - Reveal the mechanism, contradiction, or detail that changes how the viewer understands the fact.
5. PAYOFF - Deliver the concrete consequence promised by the hook. Any curiosity gap must be fully paid off before the CTA.
6. FOLLOW REASON - After delivering the payoff, include one brief invitation of at most 7 words to follow for a specific kind of explanation in this story's series lane. For example, "Follow for more space evidence." Adapt the topic to the story. Offer a repeatable editorial benefit, never an invented sequel, posting schedule, or withheld answer.
7. CTA QUESTION - End with one specific, answerable question tied to this story. This exact question is the final spoken line. Keep both closing lines brief so they do not crowd out the explanation.

=== WRITING RULES ===
- TARGET: video_script must be 65-80 words, including the follow invitation and final CTA question. Keep the finished video in the 30-35 second range. Most viewers decide by second 5, so state the central fact in scene 1 or 2 (within about 3 seconds) and spend the rest explaining it. Keep SETUP, ESCALATION and TURN to about one sentence each; cut any detail that does not serve the central fact.
- PLAIN LANGUAGE: write for a curious 14-year-old with no science background. Use at most two technical terms in the whole script, and explain each one in everyday words in the same sentence. Scene 1 must contain no technical term at all. When a mechanism is hard to picture, give one concrete everyday comparison that the article's facts support, such as metronomes on one table falling into step.
- Use concrete specifics: real names, measured numbers, places, actions, and consequences.
- Use second person only when the story directly affects the viewer. Never force "you" or "your".
- Write complete spoken sentences with varied rhythm. One brief sentence can add emphasis, but never stack fragments or one-word lines.
- Prefer active voice and everyday words. Remove throat-clearing, filler, and abstract business language.
- Every claim must be supported by the supplied article. Never heighten a fact beyond the source.
- Preserve limitations that change the meaning: distinguish a simulation from an observation, an animal study from human evidence, and a correlation from a cause. Do not turn a proposed application into an available product or a tentative signal into a confirmed discovery.
- SHORT DOES NOT MEAN STRONGER: when you cut words, cut detail, never the hedge or the condition a number depends on. Keep "may", "could", "linked to" and "in mice" when the source uses them, and keep the condition with its number ("21 percent fewer gut species for every extra 100 grams of sugar", not "sugar cuts gut species by 21 percent").
- Apply that same evidence standard to ALL hooks and cover copy. For an observational screen-time study, say the measures were linked; do not say screen time "boosts" a brain score, even with "could" or "may". A qualifier does not fix a causal claim the study cannot support.
- Lead with the fact. Do not use throat-clearing such as "here's the thing", faux-insight such as "the part everyone misses", or rhetorical labels such as "plot twist".
- Name the study, institution, researcher, report, or dataset behind a claim. Never hide behind "experts agree", "studies show", or "many argue".
- State the consequence directly. Do not add importance puffery such as "marks a pivotal moment" or trailing filler clauses beginning with "highlighting", "underscoring", "reflecting", or "showcasing".
- Use colons for real lists, labels, or quotations, never for a dramatic reveal such as "the catch: it learns".
- Repeat the clearest noun when needed instead of cycling through synonyms for style.
- Do not use negative-listing theatre such as "Not a tool. Not a toy. A revolution."
- Do not recap the script or add a fake-profound kicker before the CTA.
- Curiosity gaps and genuine questions are allowed and encouraged when they are specific and the script pays them off. You may use one curiosity-gap question before the required final CTA question.
- Use at most one exclamation mark in the full narration. Prefer none.
- Do not use em dashes or en dashes.
- Do not use clickbait formulas such as "you won't believe", "you've been lied to", "what if I told you", "nobody is talking about this", "the truth will shock you", or "wait, what?"
- Do not use binary reveal formulas such as "it's not X, it's Y" or "not just X, but Y".
- Do not use generic hype or inflated filler such as "groundbreaking", "revolutionary", "game-changing", "mind-blowing", "insane", "epic", "paradigm shift", "tapestry", "weaponized physics", or "this changes everything".
- Choose one dominant story emotion for visuals from: curious, shocking, urgent, triumphant, dark, funny. This label does not instruct the narrator to perform that emotion at maximum intensity.

=== SCENE BEATS ===
Break the script into 6-9 SCENES. For each scene provide:
- speech: the exact narration for that beat (a slice of video_script, in order). Use complete sentences rather than disconnected fragments.
- visual: a vivid concrete description of what is ON SCREEN - one clear subject, one clear action, one clear setting. Keep the main subject large and in the upper half of the frame. Do NOT mention art style or medium here (style is applied separately). Do NOT include text/words/captions (platform adds captions). Describe only physical things built from the literal science objects of this scene (cells, molecules, organs, rocks, planets, stars, instruments). Never describe a chart, graph, timeline, bar, axis, icon, badge, label, checkmark, readout or screen, and never substitute a metaphor such as plants, trees or buildings. Show an amount as a count or size of the real objects and time as those objects changing. No human hands. Never describe the background colour, lighting, glow, art style or any mascot or recurring character; the renderer adds those. For the final follow or question beat, show the story's main science object again rather than a book, desk or other everyday object.
- emotion: one of [curious, shocking, urgent, triumphant, dark, funny]
When concatenated in order, all scene.speech values must equal video_script.

=== STYLE SUGGESTION ===
Always use pixel_night_lab. The renderer applies the whole look (medium,
background, colours, lighting and any recurring character) by itself, so scene
visuals must describe only the science subject and its action.

=== HOOK VARIANTS ===
Write 3 consequence-first hook lines using different angles: what changes for the viewer or field, a vivid curiosity gap, and a counterintuitive implication. A specific question is allowed. Use 5-8 words and never more than 10: the hook must be spoken in under three seconds, so cut qualifiers that the next sentence can carry. All three must lead into the SAME setup and payoff without adding a claim absent from the article, because a user can swap the hook without rewriting the body. Then pick the strongest (best_hook_index 0/1/2). The chosen hook must be scene 1 of the scenes array and the opening of video_script.

=== PACKAGING ===
- cover_line: 3-5 punchy words that capture the central fact. Make it suitable for ALL CAPS, with no sentence punctuation. It must not claim more than video_script does: keep the script's hedge ("may", "helped", "could") instead of turning a possibility into a certainty.
- cta_question: one specific, easy-to-answer question about this story. It must also be the exact final spoken line of video_script and the final scene speech.
- hashtags: exactly 3 high-relevance hashtags, with # prefixes. Prefer story-specific search terms over generic reach tags.
- search_caption: one natural-language sentence a real person might type into TikTok search to find this exact story. Do not put the CTA or hashtags inside it.
- series_lane: classify the story as exactly one of "space", "human_body", "future_tech", or "other".
- The final posting caption will be search_caption, then cta_question, then the 3 hashtags.

=== OUTPUT (raw JSON, no markdown, no commentary) ===
{{
  "tldr": "2-3 sentence plain summary of the article",
  "bullets": ["the central fact", "only supporting details that serve that fact"],
  "hook_variants": ["Hook option 1", "Hook option 2", "Hook option 3"],
  "best_hook_index": 0,
  "dominant_emotion": "curious|shocking|urgent|triumphant|dark|funny",
  "suggested_style": "pixel_night_lab",
  "cover_line": "THREE TO FIVE WORDS",
  "cta_question": "Would you live on this planet?",
  "search_caption": "What scientists found in the atmosphere of K2-18b.",
  "series_lane": "space|human_body|future_tech|other",
  "scenes": [
    {{"speech": "...", "visual": "concrete description of what is on screen", "emotion": "..."}},
    ...
  ],
  "video_script": "full narration, concatenation of all scene.speech in order",
  "hashtags": ["#SpecificTopic", "#RelevantField", "#ExactStoryTerm"]
}}

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content[:8000]}
"""


SERIES_LANES = frozenset({"space", "human_body", "future_tech", "other"})
HOOK_MAX_WORDS = 10
SEARCH_CAPTION_MAX_CHARS = 220
CTA_QUESTION_MAX_CHARS = 220
HASHTAG_MAX_CHARS = 64
COVER_LINE_MAX_CHARS = 128

_LANE_HASHTAG_FALLBACKS = {
    "space": ("#Space", "#Astronomy", "#Science"),
    "human_body": ("#HumanBody", "#HealthScience", "#Science"),
    "future_tech": ("#FutureTech", "#Technology", "#Science"),
    "other": ("#Science", "#Research", "#LearnOnTikTok"),
}


def _clean_inline_text(value) -> str:
    """Collapse provider whitespace without inventing new copy."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_cover_line(value) -> str:
    """Return an ALL-CAPS-ready cover phrase capped at five words."""
    text = _clean_inline_text(value).strip(" \t\r\n\"'“”‘’.,!?;:")
    words = [
        word.strip(" \t\r\n\"'“”‘’.,!?;:")
        for word in text.split()
    ]
    words = [word for word in words if word][:5]
    return " ".join(words).upper()


def _normalize_question(value) -> str:
    """Normalize a CTA as one clean spoken question."""
    text = _clean_inline_text(value)
    text = re.sub(r"[\s.!?]+$", "", text)
    return f"{text}?" if text else ""


def _normalize_sentence(value) -> str:
    """Normalize search copy as a standalone natural-language sentence."""
    text = _clean_inline_text(value)
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _normalize_hashtag(value) -> str:
    text = _clean_inline_text(value).lstrip("#")
    text = re.sub(r"[^\w]", "", text, flags=re.UNICODE)
    return f"#{text}" if text else ""


def _normalize_hashtags(value, series_lane: str) -> list[str]:
    """Return exactly three unique hashtags, filling only missing slots."""
    if isinstance(value, str):
        candidates = re.split(r"[\s,]+", value)
    elif isinstance(value, (list, tuple)):
        candidates = value
    else:
        candidates = []

    normalized = []
    seen = set()
    for candidate in (*candidates, *_LANE_HASHTAG_FALLBACKS[series_lane]):
        hashtag = _normalize_hashtag(candidate)
        key = hashtag.casefold()
        if not hashtag or key in seen:
            continue
        normalized.append(hashtag)
        seen.add(key)
        if len(normalized) == 3:
            break
    return normalized


def _comparable_spoken_text(value: str) -> str:
    return re.sub(
        r"[\s.!?]+$", "", _clean_inline_text(value)
    ).casefold()


def _spoken_text_matches(left: str, right: str) -> bool:
    """Compare spoken copy while ignoring terminal punctuation/case."""
    return _comparable_spoken_text(left) == _comparable_spoken_text(right)


def _spoken_text_ends_with(text: str, ending: str) -> bool:
    """Check a spoken suffix while ignoring terminal punctuation/case."""
    return _comparable_spoken_text(text).endswith(
        _comparable_spoken_text(ending)
    )


def _ensure_spoken_ending(text: str, ending: str) -> str:
    """Make ``ending`` the exact final sentence without duplicating it."""
    spoken = _clean_inline_text(text)
    if not ending:
        return spoken
    if _spoken_text_matches(spoken, ending):
        return ending

    final_sentence = re.search(r"([^.!?]+)[.!?]*\s*$", spoken)
    if final_sentence and _spoken_text_matches(
        final_sentence.group(1), ending
    ):
        prefix = spoken[:final_sentence.start(1)].rstrip()
        return f"{prefix} {ending}".strip()

    return f"{spoken} {ending}".strip()


def _last_spoken_question(value: str) -> str:
    """Recover a CTA when a provider spoke it but omitted the JSON field."""
    text = _clean_inline_text(value)
    match = re.search(r"([^.!?]+)\?\s*$", text)
    return _normalize_question(match.group(1)) if match else ""


def parse_response(text: str) -> dict:
    """Parse AI response into the normalized summarization dict."""
    import re as _re

    text = text.strip()
    fence_match = _re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if fence_match:
        text = fence_match.group(1)

    text = text.strip()
    if not text.startswith('{'):
        brace_start = text.find('{')
        if brace_start != -1:
            text = text[brace_start:]

    depth = 0
    end = 0
    for i, ch in enumerate(text):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > 0:
        text = text[:end]

    result = json.loads(text)

    series_lane = _clean_inline_text(result.get('series_lane')).lower()
    if series_lane not in SERIES_LANES:
        series_lane = 'other'

    scenes = result.get('scenes') or []
    # Normalize scenes: each must have speech + visual; emotion is optional.
    normalized_scenes = []
    for s in scenes:
        if not isinstance(s, dict):
            continue
        speech = (s.get('speech') or '').strip()
        visual = (s.get('visual') or '').strip()
        if not (speech and visual):
            continue
        normalized_scenes.append({
            'speech': speech,
            'visual': visual,
            'emotion': (s.get('emotion') or '').strip().lower() or None,
        })

    # Recover a CTA already present in the narration when a provider omitted
    # only the dedicated field. Otherwise normalize the explicit field.
    raw_video_script = _clean_inline_text(result.get('video_script'))
    cta_question = _normalize_question(result.get('cta_question'))
    if not cta_question:
        final_spoken = (
            normalized_scenes[-1]['speech']
            if normalized_scenes
            else raw_video_script
        )
        cta_question = _last_spoken_question(final_spoken)

    # The CTA is part of the narration contract, not just posting metadata. If
    # the provider returned it separately, append it to the last scene so the
    # renderer and TTS cannot silently omit it.
    if cta_question and normalized_scenes:
        normalized_scenes[-1]['speech'] = _ensure_spoken_ending(
            normalized_scenes[-1]['speech'], cta_question
        )

    # Scenes are the source of truth whenever present. This enforces the core
    # summarizer -> renderer contract even when a provider returns a separate
    # video_script with small wording or punctuation drift.
    video_script = raw_video_script
    if normalized_scenes:
        video_script = ' '.join(s['speech'] for s in normalized_scenes)
    elif cta_question:
        video_script = _ensure_spoken_ending(video_script, cta_question)

    raw_hook_variants = result.get('hook_variants')
    if isinstance(raw_hook_variants, list):
        # Normalize provider whitespace, but preserve non-string entries so the
        # contract gate can reject them instead of silently changing meaning.
        hook_variants = [
            _clean_inline_text(variant) if isinstance(variant, str) else variant
            for variant in raw_hook_variants
        ]
    elif raw_hook_variants is None:
        hook_variants = []
    else:
        hook_variants = raw_hook_variants

    # Keep the provider's type/value intact. Coercing "1", True, or 1.7 to an
    # integer would make malformed A/B attribution look valid downstream.
    best_hook_index = result.get('best_hook_index')
    hook_variants, best_hook_index = _prune_alternate_hooks(
        hook_variants, best_hook_index, video_script
    )

    return {
        'tldr': result.get('tldr', '') or '',
        'bullets': result.get('bullets', []) or [],
        'video_script': video_script,
        'hashtags': _normalize_hashtags(result.get('hashtags'), series_lane),
        'scenes': normalized_scenes,
        'hook_variants': hook_variants,
        'best_hook_index': best_hook_index,
        'dominant_emotion': (result.get('dominant_emotion', '') or '').lower().strip(),
        # Automatic first renders always use the channel's visual identity.
        # The existing style picker remains available as an explicit override.
        'suggested_style': DEFAULT_STYLE,
        'cover_line': _normalize_cover_line(result.get('cover_line')),
        'cta_question': cta_question,
        'search_caption': _normalize_sentence(result.get('search_caption')),
        'series_lane': series_lane,
    }


def _prune_alternate_hooks(hook_variants, best_hook_index, video_script):
    """Drop alternate hooks that could never be swapped in.

    Only the chosen hook is rendered. One over-long alternate used to reject an
    otherwise valid script, and with each provider tripping a different gate no
    video could be made at all. Malformed input is returned untouched so the
    contract gate still rejects it.
    """
    if not (
        isinstance(hook_variants, list)
        and type(best_hook_index) is int
        and 0 <= best_hook_index < len(hook_variants)
        and all(isinstance(v, str) and v.strip() for v in hook_variants)
    ):
        return hook_variants, best_hook_index
    selected = hook_variants[best_hook_index]
    body_words = len(str(video_script or '').split()) - len(selected.split())
    low, high = SCRIPT_WORD_TARGET
    kept = [
        variant for index, variant in enumerate(hook_variants)
        if index == best_hook_index or (
            len(variant.split()) <= HOOK_MAX_WORDS
            and low <= body_words + len(variant.split()) <= high
        )
    ]
    return kept, kept.index(selected)


# Claude Sonnet 5 and the Opus 4.7+ family reject non-default sampling
# parameters with a 400. Kimi still wants temperature, and both share this
# helper, so the parameter has to be chosen per model rather than removed.
_NO_SAMPLING_PARAM_MODELS = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable-5",
)


# Reasoning models emit a long hidden chain of thought before the first JSON
# character, and those tokens count against max_tokens. Measured against this
# exact prompt: qwen3.7-flash spent 6,850 reasoning tokens before answering, so
# the 4,500 default truncates it mid-string on every single call.
_REASONING_MODELS = (
    "qwen3.7-flash",
    "deepseek-v4-flash",
    "glm-4.7-flash",
    "gpt-oss",
)

_DEFAULT_OUTPUT_TOKENS = 4500
_REASONING_OUTPUT_TOKENS = 16000


def _is_reasoning_model(model_id: str) -> bool:
    return any(name in model_id for name in _REASONING_MODELS)


def _openrouter_payload(model_id: str, prompt: str) -> dict:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "You are a short-form video storyteller who responds ONLY in valid JSON matching the user's schema."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": (
            _REASONING_OUTPUT_TOKENS
            if _is_reasoning_model(model_id)
            else _DEFAULT_OUTPUT_TOKENS
        ),
        "provider": OPENROUTER_PROVIDER_PREFS,
    }
    if not any(name in model_id for name in _NO_SAMPLING_PARAM_MODELS):
        payload["temperature"] = 0.75
    return payload


def _call_openrouter(model_id: str, title: str, content: str) -> dict:
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found in environment variables")

    prompt = get_prompt(title, content)

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5050",
            "X-Title": "Clipper"
        },
        json=_openrouter_payload(model_id, prompt),
        # Thinking time is wall-clock time; a reasoning model on this prompt
        # measured over a minute before returning its first byte.
        timeout=240 if _is_reasoning_model(model_id) else 90
    )

    response.raise_for_status()
    data = response.json()

    if 'error' in data:
        raise Exception(data['error'].get('message', 'Unknown OpenRouter error'))

    # A model that burns its whole budget thinking returns a null content field
    # rather than an error, so this has to be checked before .strip().
    message = (data.get('choices') or [{}])[0].get('message') or {}
    text = (message.get('content') or '').strip()
    if not text:
        raise Exception(
            f"{model_id} returned no content (likely truncated before it emitted JSON)"
        )
    return parse_response(text)


def summarize_with_kimi(title: str, content: str) -> dict:
    """Primary: Kimi K2 via OpenRouter — strong storytelling, ~$0.002/run."""
    return _call_openrouter("moonshotai/kimi-k2", title, content)


def summarize_with_qwen(title: str, content: str) -> dict:
    """Cost fallback: Qwen3.7 Flash via OpenRouter — ~$0.0013/run, ~4x cheaper
    than Kimi. A reasoning model, so it needs the larger output budget above."""
    return _call_openrouter("qwen/qwen3.7-flash", title, content)


def summarize_with_groq(title: str, content: str) -> dict:
    from groq import Groq

    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables")

    client = Groq(api_key=api_key)
    prompt = get_prompt(title, content)

    response = client.chat.completions.create(
        model=GROQ_TEXT_MODEL,
        extra_body=GROQ_EXTRA_BODY,
        messages=[
            {"role": "system", "content": "You are a short-form video storyteller who responds ONLY in valid JSON matching the user's schema."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.75,
        max_tokens=3500
    )

    text = response.choices[0].message.content.strip()
    return parse_response(text)


def _gemini_summarizer_model_name() -> str:
    return (
        os.getenv("GEMINI_SUMMARIZER_MODEL", "").strip()
        or "gemini-flash-latest"
    )


def summarize_with_gemini(title: str, content: str) -> dict:
    """Budget floor: the current Gemini Flash alias."""
    import google.generativeai as genai

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(_gemini_summarizer_model_name())
    prompt = get_prompt(title, content)

    response = model.generate_content(prompt)
    text = response.text.strip()
    return parse_response(text)


# Word-count bands for the narration. Below the target, the story is unlikely
# to fill the intended 30-35 seconds (~2.2 spoken words/s); over the warn band,
# the video runs past the length viewers stay for.
SCRIPT_WORD_TARGET = (65, 80)
SCRIPT_WORD_WARN_OVER = 88

_CLICKBAIT_PATTERNS = (
    ("you won't believe", re.compile(r"\byou (?:will not|won['’]t) believe\b", re.I)),
    ("you've been lied to", re.compile(r"\byou(?:['’]ve| have) been lied to\b", re.I)),
    ("what if I told you", re.compile(r"\bwhat if i told you\b", re.I)),
    ("nobody is talking about this", re.compile(r"\bno(?:body| one) is talking about (?:this|it)\b", re.I)),
    ("the truth will shock you", re.compile(r"\b(?:the )?truth (?:will|would) shock you\b", re.I)),
    ("wait, what", re.compile(r"\bwait\s*,?\s*what\b", re.I)),
    ("let that sink in", re.compile(r"\blet that sink in\b", re.I)),
    ("buckle up", re.compile(r"\bbuckle up\b", re.I)),
    ("here's why", re.compile(r"\bhere(?:['’]s| is) why\b", re.I)),
    (
        "manufactured 'wrong' reveal",
        re.compile(
            r"\b(?:you (?:think|thought)|your idea of)[^?]{0,80}\?\s*wrong\b",
            re.I,
        ),
    ),
)

_BINARY_CONTRAST_PATTERNS = (
    re.compile(
        r"\b(?:it|this|that)(?:['’]s|\s+is)\s+not\s+(?:just\s+)?"
        r"[^.!?]{1,80}?\s*[,;:]\s*(?:it|this|that)(?:['’]s|\s+is)\b",
        re.I,
    ),
    re.compile(
        r"\bnot\s+(?:just\s+)?[^.!?]{1,80}?\s*[,;:]\s*(?:but|instead)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:this|it)\s+(?:isn['’]t|is not|wasn['’]t|was not)\s+"
        r"[^.!?]{1,80}?[.!?]\s*(?:this|it)\s+(?:is|was)\b",
        re.I,
    ),
)

_GENERIC_HYPE_PATTERN = re.compile(
    r"\b(?:groundbreaking|revolutionary|game[- ]changing|mind[- ]blowing|"
    r"jaw[- ]dropping|unprecedented|insane|epic)\b",
    re.I,
)
_ABSTRACT_WEAPONIZED_PATTERN = re.compile(
    r"\bweaponized\s+(?:physics|science|nature|biology|psychology|technology)\b",
    re.I,
)
_CHANGES_EVERYTHING_PATTERN = re.compile(
    r"\b(?:this|it|that)\s+changes everything\b",
    re.I,
)
_EMPTY_SETUP_PATTERNS = (
    (
        "throat-clearing opener",
        re.compile(
            r"\b(?:here(?:['’]s| is) the thing|here(?:['’]s| is) what i mean|"
            r"let me be clear|i(?:['’]ll| will) be honest|"
            r"the uncomfortable truth is|it(?:['’]s| is) worth noting|"
            r"it(?:['’]s| is) important to note|let(?:['’]s| us) dive in|"
            r"let(?:['’]s| us) unpack)\b",
            re.I,
        ),
    ),
    (
        "faux-insight setup",
        re.compile(
            r"\b(?:this is the part most people skip|what most people get wrong|"
            r"here(?:['’]s| is) what nobody tells you|what nobody tells you|"
            r"the part everyone misses|the part everyone skips)\b",
            re.I,
        ),
    ),
    (
        "rhetorical setup",
        re.compile(r"\b(?:plot twist|think about it)\s*:", re.I),
    ),
)
_TARGETED_COLON_REVEAL_PATTERN = re.compile(
    r"\b(?:the (?:answer|best part|catch|detail|reason|result|secret|truth|twist)"
    r"|here(?:['’]s| is) (?:the|what)|plot twist|think about it)"
    r"\s*:\s*[a-z]",
    re.I,
)
_SUPERFICIAL_ANALYSIS_PATTERN = re.compile(
    r",\s*(?:highlighting|underscoring|reflecting|showcasing)\b",
    re.I,
)
_IMPORTANCE_PUFFERY_PATTERN = re.compile(
    r"\b(?:stands as a testament|marks a pivotal moment|plays a vital role|"
    r"solidifies (?:its|their) position|underscores (?:its|the) significance)\b",
    re.I,
)
_WEASEL_ATTRIBUTION_PATTERN = re.compile(
    r"\b(?:experts (?:agree|believe|say)|industry reports suggest|many argue|"
    r"studies show|widely regarded as)\b",
    re.I,
)
_FAKE_STRONG_VERB_PATTERN = re.compile(
    r"\b(?:acts|functions|serves)\s+as\s+(?:a|an|the)\s+"
    r"(?:centralized\s+)?(?:beacon|cornerstone|gateway|hub|testament)\b",
    re.I,
)
_NEGATIVE_LISTING_PATTERN = re.compile(
    r"\bnot\s+[^.!?]{1,60}[.!?]\s+not\s+[^.!?]{1,60}[.!?]",
    re.I,
)
_INFLATED_VOCAB_PATTERN = re.compile(
    r"\b(?:at the forefront|delve(?:s|d)?(?:\s+into)?|ever-evolving|"
    r"navigate(?:s|d)? the complexities|paradigm shift|supercharge(?:s|d)?|"
    r"tapestry)\b",
    re.I,
)
_SUMMARY_ENDING_PATTERN = re.compile(
    r"^(?:at the end of the day|in conclusion|in summary|overall|"
    r"to sum up|ultimately)\b",
    re.I,
)
_FAKE_KICKER_PATTERN = re.compile(
    r"\b(?:nature always finds a way|only time will tell|"
    r"the future is already here|the story is just beginning)\b[.!?]*$",
    re.I,
)


def _sentence_chunks(text: str) -> list[str]:
    """Return spoken sentence-like chunks without flattening punctuation."""
    return [
        chunk.strip(" \t\r\n\"'()[]")
        for chunk in re.findall(r"[^.!?]+(?:[.!?]+|$)", text)
        if chunk.strip(" \t\r\n\"'()[]")
    ]


def _final_prose_sentence(text: str) -> str:
    """Return the last prose sentence while ignoring Markdown structure."""
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip()
    ]
    final_block = paragraphs[-1] if paragraphs else text
    final_block = re.sub(r"^[#>*_\-\s]+", "", final_block)
    sentences = _sentence_chunks(final_block)
    return sentences[-1].strip() if sentences else final_block.strip()


def _find_common_quality_issues(text: str) -> list[str]:
    """Find high-signal slop patterns safe for short and long copy."""
    issues = []

    for label, pattern in _CLICKBAIT_PATTERNS:
        if pattern.search(text):
            issues.append(f"clickbait phrase: {label}")

    for label, pattern in _EMPTY_SETUP_PATTERNS:
        if pattern.search(text):
            issues.append(label)

    if any(pattern.search(text) for pattern in _BINARY_CONTRAST_PATTERNS):
        issues.append("binary contrast formula")

    if _TARGETED_COLON_REVEAL_PATTERN.search(text):
        issues.append("colon reveal")

    if _SUPERFICIAL_ANALYSIS_PATTERN.search(text):
        issues.append("superficial -ing analysis")

    if _IMPORTANCE_PUFFERY_PATTERN.search(text):
        issues.append("importance puffery")

    if _WEASEL_ATTRIBUTION_PATTERN.search(text):
        issues.append("vague attribution")

    if _FAKE_STRONG_VERB_PATTERN.search(text):
        issues.append("fake-strong verb")

    if _NEGATIVE_LISTING_PATTERN.search(text):
        issues.append("negative listing")

    hype_matches = {
        match.group(0).lower()
        for match in _GENERIC_HYPE_PATTERN.finditer(text)
    }
    weaponized_match = _ABSTRACT_WEAPONIZED_PATTERN.search(text)
    if weaponized_match:
        hype_matches.add(weaponized_match.group(0).lower())
    changes_everything_match = _CHANGES_EVERYTHING_PATTERN.search(text)
    if changes_everything_match:
        hype_matches.add(changes_everything_match.group(0).lower())
    if hype_matches:
        issues.append(f"generic hype: {', '.join(sorted(hype_matches))}")

    inflated_matches = {
        match.group(0).lower()
        for match in _INFLATED_VOCAB_PATTERN.finditer(text)
    }
    if inflated_matches:
        issues.append(
            f"inflated vocabulary: {', '.join(sorted(inflated_matches))}"
        )

    final_sentence = _final_prose_sentence(text)
    if _SUMMARY_ENDING_PATTERN.match(final_sentence):
        issues.append("summary-recap ending")
    if _FAKE_KICKER_PATTERN.search(final_sentence):
        issues.append("fake-profound ending")

    return issues


def find_script_quality_issues(script: str) -> list[str]:
    """Return deterministic anti-slop findings for a narration.

    The checker deliberately targets a small set of high-signal patterns. It
    does not try to grade the story or rewrite factual content. Keeping the
    result as a list of stable labels makes it useful in logs and unit tests,
    while leaving provider fallback in control of what gets accepted.
    """
    text = (script or '').strip()
    if not text:
        return ["missing narration"]

    issues = _find_common_quality_issues(text)

    if '—' in text or '–' in text:
        issues.append("em/en dash")

    # One consequence-first curiosity question plus the mandatory final CTA is
    # intentional. More than two questions starts to feel interrogative.
    question_count = text.count('?')
    if question_count > 2:
        issues.append(f"excessive questions: {question_count}")

    exclamation_count = text.count('!')
    if exclamation_count > 1:
        issues.append(f"excessive exclamation marks: {exclamation_count}")

    # Catch synthetic fragment stacks without rejecting a natural sequence
    # such as "The moon rose. Temperatures fell. Ice formed."
    two_word_run = 0
    three_word_run = 0
    for sentence in _sentence_chunks(text):
        word_count = len(re.findall(r"\b[\w]+(?:['’-][\w]+)?\b", sentence))
        two_word_run = two_word_run + 1 if 0 < word_count <= 2 else 0
        three_word_run = three_word_run + 1 if 0 < word_count <= 3 else 0
        if two_word_run >= 3 or three_word_run >= 4:
            issues.append("stacked sentence fragments")
            break

    return issues


def find_summary_contract_issues(result: dict) -> list[str]:
    """Validate packaging fields that downstream rendering/publishing relies on."""
    issues = []

    video_script = _clean_inline_text(result.get('video_script'))
    script_word_count = len(video_script.split())
    word_minimum, word_maximum = SCRIPT_WORD_TARGET
    if not word_minimum <= script_word_count <= word_maximum:
        issues.append(
            f"video_script must contain {word_minimum}-{word_maximum} words "
            f"(got {script_word_count})"
        )

    cover_line = _clean_inline_text(result.get('cover_line'))
    cover_word_count = len(cover_line.split())
    if not 3 <= cover_word_count <= 5:
        issues.append("cover_line must contain 3-5 words")
    if len(cover_line) > COVER_LINE_MAX_CHARS:
        issues.append(
            f"cover_line must not exceed {COVER_LINE_MAX_CHARS} characters"
        )

    cta_question = _normalize_question(result.get('cta_question'))
    if not cta_question:
        issues.append("missing cta_question")
    elif len(cta_question) > CTA_QUESTION_MAX_CHARS:
        issues.append(
            f"cta_question must not exceed "
            f"{CTA_QUESTION_MAX_CHARS} characters"
        )
    elif not _spoken_text_ends_with(
        video_script, cta_question
    ):
        issues.append("cta_question is not the final spoken line")

    scenes = result.get('scenes') or []
    final_scene = scenes[-1] if scenes and isinstance(scenes[-1], dict) else None
    if cta_question and final_scene and not _spoken_text_ends_with(
        final_scene.get('speech') or '', cta_question
    ):
        issues.append("final scene does not speak cta_question")

    hashtags = result.get('hashtags')
    if not isinstance(hashtags, list) or len(hashtags) != 3:
        issues.append("hashtags must contain exactly 3 tags")
    elif any(
        not isinstance(hashtag, str)
        or not hashtag.startswith("#")
        or len(hashtag) > HASHTAG_MAX_CHARS
        for hashtag in hashtags
    ):
        issues.append(
            f"hashtags must be # tags no longer than "
            f"{HASHTAG_MAX_CHARS} characters"
        )

    search_caption = _clean_inline_text(result.get('search_caption'))
    if not search_caption:
        issues.append("missing search_caption")
    elif len(search_caption) > SEARCH_CAPTION_MAX_CHARS:
        issues.append(
            f"search_caption must not exceed "
            f"{SEARCH_CAPTION_MAX_CHARS} characters"
        )
    elif "#" in search_caption:
        issues.append("search_caption must not contain hashtags")
    elif (
        cta_question
        and _comparable_spoken_text(cta_question)
        in _comparable_spoken_text(search_caption)
    ):
        issues.append("search_caption must not contain cta_question")

    if result.get('series_lane') not in SERIES_LANES:
        issues.append("invalid series_lane")

    hook_variants = result.get('hook_variants')
    hooks_are_valid = (
        isinstance(hook_variants, list)
        and 1 <= len(hook_variants) <= 3
        and all(
            isinstance(variant, str) and bool(variant.strip())
            for variant in hook_variants
        )
    )
    if not hooks_are_valid:
        issues.append("hook_variants must contain 1-3 nonempty strings")
    elif any(
        len(variant.split()) > HOOK_MAX_WORDS
        for variant in hook_variants
    ):
        issues.append(
            f"each hook variant must contain at most "
            f"{HOOK_MAX_WORDS} words"
        )

    best_hook_index = result.get('best_hook_index')
    best_index_is_valid = (
        type(best_hook_index) is int
        and hooks_are_valid
        and 0 <= best_hook_index < len(hook_variants)
    )
    if not best_index_is_valid:
        issues.append("best_hook_index must be a strict in-range integer")
    else:
        selected_hook = hook_variants[best_hook_index].strip()
        first_scene = (
            scenes[0]
            if scenes and isinstance(scenes[0], dict)
            else None
        )
        first_scene_speech = (
            _clean_inline_text(first_scene.get('speech'))
            if first_scene
            else ''
        )
        if first_scene_speech != selected_hook:
            issues.append("best hook must exactly match scene 1 speech")
        if not (
            video_script == selected_hook
            or video_script.startswith(f"{selected_hook} ")
        ):
            issues.append("best hook must exactly open video_script")
        else:
            body_word_count = script_word_count - len(selected_hook.split())
            if any(
                not word_minimum
                <= body_word_count + len(variant.split())
                <= word_maximum
                for variant in hook_variants
            ):
                issues.append(
                    "every hook variant must keep video_script within "
                    f"{word_minimum}-{word_maximum} words"
                )

    return issues


def find_longform_quality_issues(text: str) -> list[str]:
    """Return anti-slop findings appropriate for newsletter-length copy.

    Long-form prose can carry more questions, punctuation, and cadence changes
    than a 60-second narration, so this deliberately reuses only high-signal
    named patterns. Two or more forced micro-paragraphs are treated as
    formatting slop; a single brief paragraph can still be a human choice.
    """
    content = (text or '').strip()
    if not content:
        return ["missing newsletter copy"]

    issues = _find_common_quality_issues(content)
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", content)
        if paragraph.strip()
    ]
    micro_paragraphs = []
    for paragraph in paragraphs:
        if paragraph.startswith(('#', '>', '-', '*')):
            continue
        words = re.findall(r"\b[\w]+(?:['’-][\w]+)?\b", paragraph)
        if 0 < len(words) <= 2:
            micro_paragraphs.append(paragraph)
    if len(micro_paragraphs) >= 2:
        issues.append("stacked micro-paragraphs")

    return issues


def _log_script_stats(result: dict, provider: str) -> None:
    """Log a warning if the narration busted the word cap — useful for
    spotting providers that systematically ignore the prompt constraint."""
    script = result.get('video_script') or ''
    wc = len(script.split())
    scene_count = len(result.get('scenes') or [])
    lo, hi = SCRIPT_WORD_TARGET
    if wc < lo:
        print(
            f"[Summarizer] WARN {provider}: script is {wc} words "
            f"(target {lo}-{hi}). Delivery may be shorter than 30 seconds."
        )
    elif wc > SCRIPT_WORD_WARN_OVER:
        print(
            f"[Summarizer] WARN {provider}: script is {wc} words "
            f"(target {lo}-{hi}). Video will run past ~40 seconds."
        )
    elif wc > hi:
        print(
            f"[Summarizer] WARN {provider}: script is {wc} words "
            f"(target {lo}-{hi}). Tighten before rendering if pacing is fast."
        )
    print(f"[Summarizer] {provider} ok: {wc} words, {scene_count} scenes")


def summarize_article(title: str, content: str) -> dict:
    """Generate story-shaped video script + scene beats + style suggestion.

    Chain (quality-first with cost awareness):
      1. Kimi K2 (OpenRouter)            — primary; best $/story-quality
      2. Qwen3.7 Flash (OpenRouter)      — ~4x cheaper than Kimi; reasoning model
      3. Groq Llama 3.3 70B              — speed fallback (sub-second TTFT), free tier
      4. Gemini Flash                    — budget floor

    Returns a dict with keys:
      tldr, bullets, video_script, hashtags, scenes, hook_variants,
      best_hook_index, dominant_emotion, suggested_style, cover_line,
      cta_question, search_caption, series_lane
    """
    errors = {}

    for provider, fn in (
        ('kimi', summarize_with_kimi),
        ('qwen', summarize_with_qwen),
        ('groq', summarize_with_groq),
        ('gemini', summarize_with_gemini),
    ):
        try:
            print(f"[Summarizer] Trying {provider}...")
            result = fn(title, content)
            _log_script_stats(result, provider)
            quality_issues = find_script_quality_issues(result.get('video_script'))
            if quality_issues == ["missing narration"]:
                raise ValueError("Provider returned no narration")
            if quality_issues:
                errors[provider] = f"quality gate: {'; '.join(quality_issues)}"
                print(
                    f"[Summarizer] {provider} rejected by quality gate: "
                    f"{'; '.join(quality_issues)}"
                )
                continue
            contract_issues = find_summary_contract_issues(result)
            if contract_issues:
                errors[provider] = (
                    f"contract gate: {'; '.join(contract_issues)}"
                )
                print(
                    f"[Summarizer] {provider} rejected by contract gate: "
                    f"{'; '.join(contract_issues)}"
                )
                continue
            return result
        except Exception as e:
            errors[provider] = str(e)
            print(f"[Summarizer] {provider} failed: {e}")

    _record_summary_failure(title, errors)
    raise Exception(f"All AI providers failed: {errors}")


def _record_summary_failure(title: str, errors: dict) -> None:
    """Append why every provider failed to instance/summarizer_errors.log.

    The app only prints these reasons to stdout, which is easy to lose, so keep
    a small on-disk trail. Never let logging break the caller.
    """
    try:
        from datetime import datetime, timezone
        log_path = os.getenv("SUMMARIZER_ERROR_LOG") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "instance", "summarizer_errors.log",
        )
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {title}\n")
            for provider, reason in errors.items():
                handle.write(f"  {provider}: {str(reason)[:1500]}\n")
    except Exception:
        pass


def _strip_dashes(text: str) -> str:
    """Remove em-dashes and en-dashes as a safety net, even if the model slips.

    Em/en-dashes are the #1 AI tell in generated prose. The prompt bans them
    explicitly, but we strip any survivors here. Replacement is ', ' which
    preserves the pause without the telltale character.
    """
    if not text:
        return text
    return (
        text.replace(' — ', ', ')
            .replace('— ', ', ')
            .replace(' —', ',')
            .replace('—', ',')
            .replace(' – ', ', ')
            .replace('– ', ', ')
            .replace(' –', ',')
            .replace('–', ',')
    )


if __name__ == '__main__':
    test_result = summarize_article(
        "AI is Rewriting the Rules of Cybersecurity",
        "Artificial intelligence is transforming cybersecurity. Last year, 72% of "
        "enterprise breaches involved attackers using AI to scale phishing. "
        "Defenders are now using the same tools to spot anomalies in seconds. "
        "But a new paper from MIT shows that attackers are winning the arms race "
        "because defenders can't share data across companies fast enough."
    )
    print(json.dumps(test_result, indent=2))

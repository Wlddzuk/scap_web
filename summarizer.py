"""AI Summarization: Kimi K2 → Claude Sonnet 4.6 → Groq Llama 3.3 → Gemini 2.5 Flash.

Kimi and Claude both route through OpenRouter (one API key covers both). Groq uses
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
import requests
from dotenv import load_dotenv

load_dotenv()


def get_prompt(title: str, content: str) -> str:
    return f"""You are an elite short-form video storyteller. Think MrBeast's pacing, Johnny Harris's narrative tension, and a great manga artist's visual imagination.

Turn the article below into a 60-second vertical video script that HOOKS viewers and doesn't let go. This is a STORY, not a news summary.

=== STORY ARC (critical) ===
1. COLD OPEN (1 sentence, ~2s) - Shocking fact, contrarian claim, or "you've been lied to" moment. The viewer must feel "wait what?" inside 1.5 seconds. DO NOT introduce the topic politely.
2. STAKES (2-3 sentences, 4-6s) - Who or what is at risk. Make it personal with "you"/"your".
3. TURN (2-3 sentences, 5-8s) - The twist, the reveal, the reframe.
4. PAYOFF (3-4 sentences, 10-15s) - Rapid-fire specifics: names, numbers, places, consequences.
5. LOOP (1 sentence, ~2s) - Mic-drop line that either answers the cold open or asks a question that earns a rewatch.

=== WRITING RULES ===
- HARD LIMIT: video_script must be 130-150 words. Count every word. Anything over 160 is WRONG and will be rejected — a 60-second TikTok at natural pacing is at most 150 words. If you need to cut, drop the weakest PAYOFF sentence, not the hook or loop.
- Second person: "you", "your" - never "people" or "they".
- Short sentences. Every line should be tweetable.
- CONCRETE specifics. Real names, real numbers, real places. Zero generalities.
- Every line must be something a viewer can PICTURE. No abstract nouns ("implications", "ramifications").
- One dominant emotion throughout. Pick ONE of: curious, shocking, urgent, triumphant, dark, funny.

=== SCENE BEATS ===
Break the script into 10-14 SCENES. For each scene provide:
- speech: the exact narration for that beat (a slice of video_script, in order)
- visual: a vivid concrete description of what is ON SCREEN - one clear subject, one clear action, one clear setting. Do NOT mention art style or medium here (style is applied separately). Do NOT include text/words/captions (platform adds captions).
- emotion: one of [curious, shocking, urgent, triumphant, dark, funny]

When concatenated in order, all scene.speech values must equal video_script.

=== STYLE SUGGESTION ===
Pick the one visual style that will make THIS story most scroll-stopping:
- manga: conflict, drama, action, discovery
- anime_vibrant: wonder, future, tech, hopeful
- cinematic: serious news, drama, biography, science
- comic: action, hero, sports, triumph
- 3d_pixar: wholesome, fun, education, general
- retro_synthwave: tech, future, gaming, crypto
- documentary: human stories, politics, investigation
- noir: crime, mystery, scandal, dark

=== HOOK VARIANTS ===
Write 3 alternative COLD OPEN lines (different angles: question, stat shock, contrarian claim). Then pick the strongest (best_hook_index 0/1/2). The chosen hook must be scene 1 of the scenes array and the opening of video_script.

=== OUTPUT (raw JSON, no markdown, no commentary) ===
{{
  "tldr": "2-3 sentence plain summary of the article",
  "bullets": ["five key points", "...", "...", "...", "..."],
  "hook_variants": ["Hook option 1", "Hook option 2", "Hook option 3"],
  "best_hook_index": 0,
  "dominant_emotion": "curious|shocking|urgent|triumphant|dark|funny",
  "suggested_style": "manga|anime_vibrant|cinematic|comic|3d_pixar|retro_synthwave|documentary|noir",
  "scenes": [
    {{"speech": "...", "visual": "concrete description of what is on screen", "emotion": "..."}},
    ...
  ],
  "video_script": "full narration, concatenation of all scene.speech in order",
  "hashtags": ["#One", "#Two", "#Three", "#Four", "#Five"]
}}

ARTICLE TITLE: {title}

ARTICLE CONTENT:
{content[:8000]}
"""


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

    # If video_script missing, reconstruct from scenes
    video_script = (result.get('video_script') or '').strip()
    if not video_script and normalized_scenes:
        video_script = ' '.join(s['speech'] for s in normalized_scenes)

    return {
        'tldr': result.get('tldr', '') or '',
        'bullets': result.get('bullets', []) or [],
        'video_script': video_script,
        'hashtags': result.get('hashtags', []) or [],
        'scenes': normalized_scenes,
        'hook_variants': result.get('hook_variants', []) or [],
        'best_hook_index': int(result.get('best_hook_index', 0) or 0),
        'dominant_emotion': (result.get('dominant_emotion', '') or '').lower().strip(),
        'suggested_style': (result.get('suggested_style', '') or '').lower().strip(),
    }


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
        json={
            "model": model_id,
            "messages": [
                {"role": "system", "content": "You are a short-form video storyteller who responds ONLY in valid JSON matching the user's schema."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.75,
            "max_tokens": 3500
        },
        timeout=90
    )

    response.raise_for_status()
    data = response.json()

    if 'error' in data:
        raise Exception(data['error'].get('message', 'Unknown OpenRouter error'))

    text = data['choices'][0]['message']['content'].strip()
    return parse_response(text)


def summarize_with_kimi(title: str, content: str) -> dict:
    """Primary: Kimi K2 via OpenRouter — strong storytelling, ~$0.002/run."""
    return _call_openrouter("moonshotai/kimi-k2", title, content)


def summarize_with_claude(title: str, content: str) -> dict:
    """Quality fallback: Claude Sonnet 4.6 via OpenRouter — best hooks, ~$0.015/run."""
    return _call_openrouter("anthropic/claude-sonnet-4.6", title, content)


def summarize_with_groq(title: str, content: str) -> dict:
    from groq import Groq

    api_key = os.getenv('GROQ_API_KEY')
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment variables")

    client = Groq(api_key=api_key)
    prompt = get_prompt(title, content)

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a short-form video storyteller who responds ONLY in valid JSON matching the user's schema."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.75,
        max_tokens=3500
    )

    text = response.choices[0].message.content.strip()
    return parse_response(text)


def summarize_with_gemini(title: str, content: str) -> dict:
    """Budget floor: Gemini 2.5 Flash — ~$0.0005/run."""
    import google.generativeai as genai

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash')
    prompt = get_prompt(title, content)

    response = model.generate_content(prompt)
    text = response.text.strip()
    return parse_response(text)


# Word-count bands for the narration. Over the warn band, TikTok delivery
# gets rushed; over the hard band, we're well past the 60s target.
SCRIPT_WORD_TARGET = (130, 150)
SCRIPT_WORD_WARN_OVER = 160


def _log_script_stats(result: dict, provider: str) -> None:
    """Log a warning if the narration busted the word cap — useful for
    spotting providers that systematically ignore the prompt constraint."""
    script = result.get('video_script') or ''
    wc = len(script.split())
    scene_count = len(result.get('scenes') or [])
    lo, hi = SCRIPT_WORD_TARGET
    if wc > SCRIPT_WORD_WARN_OVER:
        print(
            f"[Summarizer] WARN {provider}: script is {wc} words "
            f"(target {lo}-{hi}). Delivery will feel rushed at ~60s."
        )
    print(f"[Summarizer] {provider} ok: {wc} words, {scene_count} scenes")


def summarize_article(title: str, content: str) -> dict:
    """Generate story-shaped video script + scene beats + style suggestion.

    Chain (quality-first with cost awareness):
      1. Kimi K2 (OpenRouter)            — primary; best $/story-quality
      2. Claude Sonnet 4.6 (OpenRouter)  — quality fallback when Kimi hiccups
      3. Groq Llama 3.3 70B              — speed fallback (sub-second TTFT)
      4. Gemini 2.5 Flash                — budget floor

    Returns a dict with keys:
      tldr, bullets, video_script, hashtags, scenes, hook_variants,
      best_hook_index, dominant_emotion, suggested_style
    """
    errors = {}

    for provider, fn in (
        ('kimi', summarize_with_kimi),
        ('claude', summarize_with_claude),
        ('groq', summarize_with_groq),
        ('gemini', summarize_with_gemini),
    ):
        try:
            print(f"[Summarizer] Trying {provider}...")
            result = fn(title, content)
            _log_script_stats(result, provider)
            return result
        except Exception as e:
            errors[provider] = str(e)
            print(f"[Summarizer] {provider} failed: {e}")

    raise Exception(f"All AI providers failed: {errors}")


def get_substack_prompt(title: str, site_name: str, tldr: str, bullets: list,
                        hook_variants: list, scenes: list, dominant_emotion: str) -> str:
    bullets_text = '\n'.join(f'- {b}' for b in (bullets or []))
    hooks_text = '\n'.join(f'- {h}' for h in (hook_variants or [])[:3])
    story_beats = ' '.join(s.get('speech', '') for s in (scenes or []))

    return f"""Write a long-form Substack newsletter post (600–900 words) about this article.

Article: {title}
Source: {site_name or 'Unknown'}
Summary: {tldr}
Key points:
{bullets_text}
Hook angles (inspiration only — do NOT copy verbatim):
{hooks_text}
Overall emotion: {dominant_emotion or 'curious'}
Story beats from video:
{story_beats}

=== RULES ===
1. OPENING: Start with a relatable everyday analogy or surprising question. NOT the hook verbatim. Give readers a "huh, I never thought of it that way" moment in the first two sentences.
2. VOICE: Warm, conversational second-person ("you", "we"). Write like a smart friend who just read this article on the train and can't wait to tell you about it.
3. ANALOGIES: Every technical concept must have a real-world comparison. If a 300-qubit chip beats a computer made of every atom in the universe, compare that to something absurd and relatable — a sports team beating every team that ever existed simultaneously.
4. STRUCTURE: 3–4 body sections, each with a punchy ## Subheading. No walls of text — short paragraphs (2–4 sentences max).
5. CALLOUT: One > blockquote for the single most mind-blowing insight. Make it the thing readers screenshot.
6. CONCLUSION: End with a thought-provoking discussion question that invites comments. Not rhetorical — genuinely curious.
7. SPECIFICS: Real names, real numbers, relatable scale comparisons. Vague is boring.
8. LENGTH: 600–900 words in the body. Readable in 4–6 minutes.

=== OUTPUT ===
Respond ONLY with valid JSON (no markdown fences):
{{
  "post_title": "catchy title that promises something different from the original article headline",
  "subtitle": "one sentence that completes the promise of the title",
  "body": "full post body in markdown — ## headings, **bold** for emphasis, > callout block, blank lines between paragraphs"
}}"""


def generate_substack_post(article) -> str:
    """Generate a long-form Substack companion post for an article.

    Reuses the OpenRouter provider chain (Kimi K2 → Claude Sonnet 4.6).
    Returns the assembled post as a single string ready for clipboard.
    """
    import json as _json

    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not found in environment variables")

    bullets = _json.loads(article.bullets) if article.bullets else []
    hook_variants = _json.loads(article.hook_variants) if article.hook_variants else []
    scenes = _json.loads(article.scenes) if article.scenes else []

    prompt = get_substack_prompt(
        title=article.title,
        site_name=article.site_name or '',
        tldr=article.tldr or '',
        bullets=bullets,
        hook_variants=hook_variants,
        scenes=scenes,
        dominant_emotion=article.dominant_emotion or 'curious',
    )

    errors = {}
    for model_id, label in (
        ("moonshotai/kimi-k2", "kimi"),
        ("anthropic/claude-sonnet-4.6", "claude"),
    ):
        try:
            print(f"[Substack] Trying {label}...")
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5050",
                    "X-Title": "Clipper"
                },
                json={
                    "model": model_id,
                    "messages": [
                        {"role": "system", "content": "You are a Substack newsletter writer who makes complex ideas click for everyone using everyday analogies. Respond ONLY with valid JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.80,
                    "max_tokens": 4000
                },
                timeout=120
            )
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                raise Exception(data['error'].get('message', 'Unknown error'))

            raw = data['choices'][0]['message']['content'].strip()
            # Strip markdown fences if the model wraps in ```json ... ```
            if raw.startswith('```'):
                raw = raw.split('```', 2)[1]
                if raw.startswith('json'):
                    raw = raw[4:]
                raw = raw.rsplit('```', 1)[0].strip()

            parsed = _json.loads(raw)
            post_title = parsed.get('post_title', article.title)
            subtitle = parsed.get('subtitle', '')
            body = parsed.get('body', '')

            assembled = f"# {post_title}\n*{subtitle}*\n\n{body}" if subtitle else f"# {post_title}\n\n{body}"
            print(f"[Substack] {label} ok: {len(assembled.split())} words")
            return assembled

        except Exception as e:
            errors[label] = str(e)
            print(f"[Substack] {label} failed: {e}")

    raise Exception(f"Substack generation failed on all providers: {errors}")


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

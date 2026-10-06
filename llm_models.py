"""Groq model id shared by every Groq call site.

Groq retired llama-3.3-70b-versatile, and nine hardcoded references to it all
failed with 404: script writing, story ranking, style picking, image prompts
and carousels degraded silently to defaults. Keep the id in one place.
"""

import os

GROQ_TEXT_MODEL = (
    os.getenv("GROQ_TEXT_MODEL", "openai/gpt-oss-120b").strip()
    or "openai/gpt-oss-120b"
)

# gpt-oss reasons before answering and those tokens count against max_tokens.
# Low effort keeps it to a few dozen tokens so small budgets stay usable.
GROQ_EXTRA_BODY = (
    {"reasoning_effort": "low"}
    if GROQ_TEXT_MODEL.startswith("openai/gpt-oss")
    else {}
)


def _price_env(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


# Hard ceiling sent with every OpenRouter request, in USD per million tokens.
# OpenRouter refuses to route to any provider above it, so a model swap or a
# pricier provider can never silently raise the cost of a story. Kimi K2
# ($0.57/$2.30) is the most expensive model Clipper uses and fits under it.
OPENROUTER_PROVIDER_PREFS = {
    "max_price": {
        "prompt": _price_env("OPENROUTER_MAX_PROMPT_PRICE", 1.0),
        "completion": _price_env("OPENROUTER_MAX_COMPLETION_PRICE", 3.0),
    },
}

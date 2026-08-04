"""
Visual style presets for video generation.

Each preset produces a consistent visual identity across all images in a video.
Applied on top of per-scene visual concepts (which describe WHAT is on screen)
to add the HOW (medium, lighting, aesthetic).

Usage:
    from visual_styles import apply_style, auto_pick_style, list_styles

    final_prompt = apply_style("lone figure walks through neon-lit alley",
                               style_key="noir", is_hook=True)
"""

import os
import re


STYLES = {
    "illustrated_science": {
        "name": "Illustrated Science",
        "emoji": "🔬",
        "description": "Flat editorial science schematics for safe, non-photographic scenes",
        "palette": ["#f4f0e6", "#145da0", "#f4c542", "#d64541"],
        "base": (
            "intentional hand-drawn editorial science illustration on warm off-white "
            "paper, confident cobalt-blue ink contours and simplified blue shapes, "
            "one selective yellow focus highlight, restrained red instructional accent, "
            "clean schematic composition, expressive and memorable without looking childish, "
            "one clear central subject, generous negative space, premium "
            "animated documentary art direction, clearly an illustration, flat 2D artwork, "
            "vertical 9:16, no photography, no photorealism, no 3D render, no glossy CGI, "
            "no hands, no faces, no crowds, no signatures, no watermarks, no text, no labels, "
            "no words, no cutaway, no cross-section, no microscopy, no measurement scale"
        ),
        "hook_modifier": (
            "bold consequence-first composition, partially revealed focal detail, "
            "strong silhouette, immediate visual question, clean open annotation space"
        ),
        "good_for": ["science", "education", "discovery", "nature", "space", "health"],
    },
    "manga": {
        "name": "Manga / Anime Panel",
        "emoji": "📘",
        "description": "Black & white manga panels with dramatic action lines and selective color",
        "palette": ["#0a0a0a", "#ffffff", "#e63946", "#f1faee"],
        "base": (
            "manga panel illustration, bold ink line work, screentone shading, "
            "dramatic radial action lines, high contrast black and white with one "
            "accent color pop, expressive dynamic poses, Jujutsu Kaisen meets Akira "
            "aesthetic, sharp clean linework, cinematic panel composition, "
            "professional manga art, vertical 9:16, no text no speech bubbles no words"
        ),
        "hook_modifier": (
            "extreme dutch angle splash panel, radial motion lines, impact frame, "
            "dramatic foreshortening, ink splatter, intense shocked expression"
        ),
        "good_for": ["conflict", "drama", "action", "tech", "discovery", "mystery"],
    },
    "anime_vibrant": {
        "name": "Vibrant Anime",
        "emoji": "🌸",
        "description": "Colorful cel-shaded anime with glowing accents",
        "palette": ["#ff006e", "#8338ec", "#3a86ff", "#ffbe0b"],
        "base": (
            "vibrant anime key visual, cel-shaded illustration, Makoto Shinkai meets "
            "Studio Trigger aesthetic, volumetric god rays, neon rim lighting, "
            "dynamic sky, expressive characters, highly saturated colors, "
            "ultra detailed, cinematic composition, vertical 9:16, no text no words"
        ),
        "hook_modifier": (
            "explosive action pose, radiant speed lines, chromatic lens flare, "
            "glowing particle effects, electric energy burst"
        ),
        "good_for": ["wonder", "future", "tech", "youth", "adventure", "hopeful"],
    },
    "cinematic": {
        "name": "Cinematic Film",
        "emoji": "🎬",
        "description": "Blockbuster movie stills, anamorphic lens, teal & orange grade",
        "palette": ["#0d1b2a", "#e9c46a", "#f4a261", "#e76f51"],
        "base": (
            "cinematic film still, anamorphic 2.39:1 framing, shallow depth of field, "
            "volumetric lighting, teal and orange color grade, Roger Deakins "
            "cinematography, IMAX quality, subtle film grain, Hollywood blockbuster "
            "aesthetic, ultra realistic, 8k detail, vertical 9:16, no text"
        ),
        "hook_modifier": (
            "extreme close-up, dramatic rim lighting, shallow focus, film grain, "
            "cinematic bokeh, intense gaze"
        ),
        "good_for": ["drama", "news", "serious", "science", "biography", "history"],
    },
    "comic": {
        "name": "Comic Book",
        "emoji": "💥",
        "description": "Bold comic panels, halftone shading, saturated primaries",
        "palette": ["#ff0033", "#ffce00", "#0066ff", "#111111"],
        "base": (
            "bold comic book splash page art, dynamic action composition, halftone "
            "ben-day dot shading, thick black ink outlines, saturated primary colors, "
            "dramatic forced perspective, Jim Lee meets Frank Miller, heroic poses, "
            "high energy kinetic composition, vertical 9:16, no text no speech bubbles"
        ),
        "hook_modifier": (
            "splash page, extreme foreshortening, kinetic energy, "
            "dramatic skyline backdrop"
        ),
        "good_for": ["action", "hero", "sports", "drama", "conflict", "triumph"],
    },
    "3d_pixar": {
        "name": "3D Animated",
        "emoji": "✨",
        "description": "Pixar-style 3D with warm vibrant colors and soft lighting",
        "palette": ["#ff9f1c", "#2ec4b6", "#e71d36", "#fdfffc"],
        "base": (
            "3D Pixar-style render, warm vibrant colors, expressive exaggerated "
            "characters, subsurface scattering, soft ambient occlusion, cinematic "
            "three-point lighting, Disney Dreamworks quality, highly detailed "
            "textures, charming stylized composition, vertical 9:16, no text"
        ),
        "hook_modifier": (
            "dramatic close-up, soft rim lighting, expressive eyes, pop composition"
        ),
        "good_for": ["fun", "general", "education", "kids", "wholesome", "curious"],
    },
    "retro_synthwave": {
        "name": "Retro Synthwave",
        "emoji": "🌆",
        "description": "80s neon, chrome reflections, sunset grid horizon",
        "palette": ["#ff006e", "#8338ec", "#00f5d4", "#1a1a2e"],
        "base": (
            "retro 80s synthwave aesthetic, neon magenta and cyan glow, chrome "
            "reflections, sunset grid horizon, vaporwave palette, VHS scan lines, "
            "Miami Vice meets Blade Runner 2049, bold geometric composition, "
            "retrofuturistic, cinematic, vertical 9:16, no text"
        ),
        "hook_modifier": (
            "neon sunset grid, chrome title-frame glow, scanline flicker, "
            "holographic particle burst"
        ),
        "good_for": ["tech", "future", "nostalgia", "music", "gaming", "crypto"],
    },
    "documentary": {
        "name": "Documentary Photo",
        "emoji": "📷",
        "description": "Photojournalism — raw, authentic, decisive moment",
        "palette": ["#2b2d42", "#8d99ae", "#edf2f4", "#d90429"],
        "base": (
            "documentary photography, Magnum photojournalism aesthetic, natural "
            "available light, candid decisive moment, Sony A7 35mm prime, shallow "
            "depth of field, authentic raw emotion, Pulitzer-worthy composition, "
            "grainy photographic realism, vertical 9:16, no text"
        ),
        "hook_modifier": (
            "intimate close-up portrait, direct eye contact, available window light, "
            "emotional authenticity"
        ),
        "good_for": ["news", "politics", "human", "social", "investigation", "real"],
    },
    "noir": {
        "name": "Film Noir",
        "emoji": "🌑",
        "description": "High-contrast B&W, venetian shadows, 1940s detective",
        "palette": ["#000000", "#ffffff", "#c9184a", "#343a40"],
        "base": (
            "film noir black and white cinematography, extreme chiaroscuro lighting, "
            "dramatic venetian blind shadows, 1940s detective aesthetic, rain-slicked "
            "streets, cigarette smoke haze, Orson Welles composition, high contrast, "
            "moody atmosphere, 35mm film grain, vertical 9:16, no text"
        ),
        "hook_modifier": (
            "silhouette against window, extreme backlight, single spot key-light, "
            "mysterious mood"
        ),
        "good_for": ["crime", "mystery", "investigation", "scandal", "dark"],
    },
}

DEFAULT_STYLE = "illustrated_science"


def list_styles() -> list:
    """Return style metadata for UI consumption."""
    return [
        {
            "key": k,
            "name": v["name"],
            "emoji": v["emoji"],
            "description": v["description"],
            "palette": v["palette"],
        }
        for k, v in STYLES.items()
    ]


def get_style(key: str) -> dict:
    """Get a style dict by key, or the default if unknown."""
    return STYLES.get(key) or STYLES[DEFAULT_STYLE]


def apply_style(visual_concept: str, style_key: str, is_hook: bool = False) -> str:
    """Compose a full image prompt from a scene's visual concept + style.

    The visual_concept describes WHAT is on screen (subject, action, setting).
    The style provides HOW it looks (medium, lighting, aesthetic).
    """
    style = get_style(style_key)
    concept = (visual_concept or "").strip().rstrip(",.")
    parts = [concept] if concept else []
    if is_hook:
        parts.append(style["hook_modifier"])
    parts.append(style["base"])
    return ", ".join(p for p in parts if p)


def auto_pick_style(title: str, script: str) -> str:
    """Return the brand's automatic first-render style.

    The manual picker can still override this. Keeping automatic generation on
    one recognizable visual language also avoids an extra LLM call before the
    first paid image request.
    """
    return DEFAULT_STYLE


def _legacy_auto_pick_style(title: str, script: str) -> str:
    """Legacy content-aware picker retained for explicit future experiments."""
    try:
        from groq import Groq
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            return DEFAULT_STYLE

        client = Groq(api_key=api_key)
        style_list = "\n".join(
            f"- {k}: {v['description']} (good for: {', '.join(v['good_for'])})"
            for k, v in STYLES.items()
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You pick a visual style for short-form videos. Respond with ONLY one style key from the list. No explanation, no punctuation."},
                {"role": "user", "content": (
                    f"TITLE: {title}\n\nSCRIPT: {script[:1500]}\n\n"
                    f"AVAILABLE STYLES:\n{style_list}\n\n"
                    "Pick the ONE style that will make this video most scroll-stopping. "
                    "Match tone to style: conflict/drama → manga; crime/mystery → noir; "
                    "tech/future → retro_synthwave or anime_vibrant; serious news → cinematic "
                    "or documentary; triumphant/action → comic; wholesome/fun → 3d_pixar.\n\n"
                    "Respond with ONLY the style key."
                )}
            ],
            temperature=0.3,
            max_tokens=20,
        )
        raw = response.choices[0].message.content.strip().lower()
        cleaned = re.sub(r"[^a-z_0-9]", "", raw)
        if cleaned in STYLES:
            print(f"[Style] Auto-picked: {cleaned}")
            return cleaned
        # Try prefix match in case model added fluff
        for k in STYLES:
            if cleaned.startswith(k) or k in cleaned:
                print(f"[Style] Auto-picked (loose): {k}")
                return k
    except Exception as e:
        print(f"[Style] Auto-pick failed: {e}")
    return DEFAULT_STYLE


if __name__ == "__main__":
    import json
    print(json.dumps(list_styles(), indent=2))
    print("\nSample prompt (manga, hook):")
    print(apply_style("lone hacker types frantically as red alert flashes", "manga", is_hook=True))

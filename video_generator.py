"""
TikTok-Style Video Generator - Parallel image generation + fast rendering.
"""

import os
import re
import time
import json
import logging
import math
import random
import threading
from datetime import datetime
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

from groq import Groq
import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw, ImageFont
import requests
from dotenv import load_dotenv
import tts_engine

load_dotenv()

logger = logging.getLogger(__name__)

# Bound Hugging Face model metadata/download requests used by the lazy Kokoro
# and faster-whisper model loaders. The caption pipeline still retries once and
# falls back to a text-free render if the Whisper model cannot be loaded.
try:
    _model_download_timeout = max(1, int(os.getenv("WHISPER_DOWNLOAD_TIMEOUT", "60")))
except ValueError:
    _model_download_timeout = 60
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", str(_model_download_timeout))

# Video settings
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
FPS = 30

# Hook settings
HOOK_DURATION = 5.0
NUM_HOOK_IMAGES = 4

# Body image count (reduced from 20 for speed)
NUM_BODY_IMAGES = 14

# Parallel image generation workers
MAX_IMAGE_WORKERS = 6

# Timing constraints
MIN_CHUNK_DURATION = 1.2
MAX_CHUNK_DURATION = 3.2
DEFAULT_CHUNK_DURATION = 2.5
DEFAULT_WORDS_PER_CHUNK = 4
RETRY_ATTEMPTS = 2

# Caption settings
CAPTION_FONT_PATH = Path(__file__).resolve().parent / "static" / "fonts" / "Montserrat-Variable.ttf"
CAPTION_FONT_SIZE = 88
CAPTION_MIN_FONT_SIZE = 56
CAPTION_STROKE_WIDTH = 7
CAPTION_SIDE_MARGIN = 80
CAPTION_SAFE_BOTTOM = 1500
CAPTION_POP_DURATION = 0.1
HEADLINE_DURATION = 2.5
WHISPER_MODELS = {"tiny", "base"}

_WHISPER_MODEL = None
_WHISPER_MODEL_NAME = None
_WHISPER_LOCK = threading.Lock()

# Pillow 10+ compatibility
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


def generate_image_fal(prompt: str, retry_count: int = RETRY_ATTEMPTS) -> Image.Image:
    """Generate image using FAL.ai FLUX model."""
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        logger.info("[Image] No FAL_KEY, using gradient")
        return create_gradient_background()

    enhanced_prompt = (
        f"{prompt}, vibrant bright colors, high contrast, eye-catching, "
        f"clean composition, vertical 9:16, professional quality, no text no words"
    )

    for attempt in range(retry_count):
        try:
            logger.info(f"[Image] Generating: {prompt[:40]}...")
            result = fal_client.run(
                "fal-ai/flux/schnell",
                arguments={
                    "prompt": enhanced_prompt,
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "num_inference_steps": 4
                }
            )

            if result and "images" in result and result["images"]:
                image_url = result["images"][0]["url"]
                response = requests.get(image_url, timeout=30)
                img = Image.open(BytesIO(response.content)).convert("RGB")
                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
                logger.info("[Image] Generated")
                return img

        except Exception as e:
            logger.info(f"[Image] Attempt {attempt + 1} failed: {e}")
            time.sleep(2)

    logger.info("[Image] Failed, using gradient")
    return create_gradient_background()


def search_pexels_images(query: str, num_images: int, orientation: str = "portrait") -> list:
    """Search Pexels for stock photos matching the query.

    Returns a list of PIL Images resized to VIDEO_WIDTH x VIDEO_HEIGHT.
    Falls back to gradient backgrounds if Pexels key is missing or search fails.
    """
    api_key = os.getenv("PEXELS_API_KEY")
    if not api_key:
        logger.info("[Pexels] No PEXELS_API_KEY set, using gradients")
        return [create_gradient_background() for _ in range(num_images)]

    try:
        logger.info(f"[Pexels] Searching: '{query[:50]}' ({num_images} images)...")
        headers = {"Authorization": api_key}
        params = {
            "query": query,
            "per_page": min(num_images * 2, 80),  # fetch extra for variety
            "orientation": orientation,
            "size": "large",
        }
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        photos = data.get("photos", [])
        if not photos:
            logger.info(f"[Pexels] No results for '{query}', trying shorter query")
            # Retry with just first 2 words
            short_query = " ".join(query.split()[:2])
            params["query"] = short_query
            resp = requests.get(
                "https://api.pexels.com/v1/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            photos = data.get("photos", [])

        if not photos:
            logger.info("[Pexels] Still no results, using gradients")
            return [create_gradient_background() for _ in range(num_images)]

        # Shuffle to get variety, then take num_images
        random.shuffle(photos)
        selected = photos[:num_images]

        # Download images in parallel
        images = []

        def _download_pexels(photo):
            # Use portrait or large2x for best quality
            url = photo.get("src", {}).get("portrait") or photo.get("src", {}).get("large2x")
            if not url:
                return create_gradient_background()
            try:
                r = requests.get(url, timeout=30)
                img = Image.open(BytesIO(r.content)).convert("RGB")
                return resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
            except Exception as e:
                logger.info(f"[Pexels] Download failed: {e}")
                return create_gradient_background()

        with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
            futures = [executor.submit(_download_pexels, p) for p in selected]
            for f in futures:
                images.append(f.result())

        # Pad with gradients if not enough
        while len(images) < num_images:
            images.append(create_gradient_background())

        logger.info(f"[Pexels] Got {len(images)} images")
        return images[:num_images]

    except Exception as e:
        logger.info(f"[Pexels] Error: {e}")
        return [create_gradient_background() for _ in range(num_images)]


def _extract_search_keywords(title: str, script: str) -> list:
    """Use Groq to extract good search keywords from the article for stock photo search."""
    client = get_groq_client()
    if not client:
        # Fallback: use title words
        return [title]

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Extract 5-8 short, vivid search terms for finding stock photos "
                        "that would illustrate this article visually. Each term should be "
                        "1-3 words, suitable for a stock photo search. Respond with JSON array."
                    ),
                },
                {
                    "role": "user",
                    "content": f"TITLE: {title}\nSCRIPT: {script[:2000]}\n\nReturn JSON array of search terms.",
                },
            ],
            temperature=0.5,
            max_tokens=200,
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            terms = json.loads(match.group(0))
            if isinstance(terms, list) and terms:
                logger.info(f"[Pexels] Keywords: {terms[:5]}")
                return [str(t) for t in terms]
    except Exception as e:
        logger.info(f"[Pexels] Keyword extraction failed: {e}")

    return [title]


def create_gradient_background() -> Image.Image:
    """Fallback gradient background."""
    img = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(VIDEO_HEIGHT):
        ratio = y / VIDEO_HEIGHT
        r, g, b = int(20 + ratio * 10), int(10 + ratio * 20), int(40 + ratio * 50)
        draw.line([(0, y), (VIDEO_WIDTH, y)], fill=(r, g, b))
    return img


def resize_and_crop_image(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """Resize and center-crop to target dimensions."""
    orig_w, orig_h = img.size
    orig_ratio = orig_w / orig_h
    target_ratio = target_width / target_height

    if orig_ratio > target_ratio:
        new_h, new_w = orig_h, int(orig_h * target_ratio)
        left = (orig_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, new_h))
    else:
        new_w, new_h = orig_w, int(orig_w / target_ratio)
        top = (orig_h - new_h) // 2
        img = img.crop((0, top, new_w, top + new_h))

    return img.resize((target_width, target_height), Image.LANCZOS)


def clean_text(text: str) -> str:
    """Remove bracket tags and normalize whitespace."""
    text = re.sub(r"\[.*?\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _get_whisper_model(model_name: str):
    """Load and cache a CPU-only faster-whisper model."""
    global _WHISPER_MODEL, _WHISPER_MODEL_NAME

    if _WHISPER_MODEL is not None and _WHISPER_MODEL_NAME == model_name:
        return _WHISPER_MODEL

    from faster_whisper import WhisperModel

    try:
        cpu_threads = max(1, int(os.getenv("WHISPER_CPU_THREADS", "2")))
    except ValueError:
        cpu_threads = 2

    _WHISPER_MODEL = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
    )
    _WHISPER_MODEL_NAME = model_name
    return _WHISPER_MODEL


def transcribe_word_timestamps(
    audio_path: str,
    model_name: str = None,
    script_text: str = "",
) -> list:
    """Return word-level timings from faster-whisper, or an empty list on failure.

    Whisper is deliberately isolated behind this graceful fallback so a missing
    model, failed download, or transcription error never prevents video output.
    """
    requested_model = (model_name or os.getenv("WHISPER_MODEL", "tiny")).strip().lower()
    if requested_model not in WHISPER_MODELS:
        logger.warning("[Captions] Unsupported WHISPER_MODEL=%s; using tiny", requested_model)
        requested_model = "tiny"

    with _WHISPER_LOCK:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                logger.info(
                    "[Captions] Transcribing with faster-whisper %s (attempt %d/%d)...",
                    requested_model,
                    attempt + 1,
                    RETRY_ATTEMPTS,
                )
                model = _get_whisper_model(requested_model)
                segments, _info = model.transcribe(
                    audio_path,
                    language="en",
                    beam_size=1,
                    condition_on_previous_text=False,
                    initial_prompt=(script_text or "")[:800] or None,
                    vad_filter=True,
                    word_timestamps=True,
                )

                words = []
                for segment in segments:
                    for word in segment.words or []:
                        text = (word.word or "").strip()
                        if not text or word.start is None or word.end is None:
                            continue
                        start = max(0.0, float(word.start))
                        end = max(start + 0.05, float(word.end))
                        words.append({"text": text, "start": start, "end": end})

                logger.info("[Captions] Transcribed %d timed words", len(words))
                return words
            except Exception as exc:
                logger.warning(
                    "[Captions] Transcription attempt %d failed: %s",
                    attempt + 1,
                    exc,
                )
                if attempt + 1 < RETRY_ATTEMPTS:
                    time.sleep(1)

    logger.warning("[Captions] Continuing without word-synced captions")
    return []


def group_words_for_captions(words: list, min_words: int = 2, max_words: int = 4) -> list:
    """Group timed words into short TikTok-style caption cues."""
    if not words:
        return []

    grouped_words = []
    current = []

    for index, word in enumerate(words):
        current.append(word)
        next_word = words[index + 1] if index + 1 < len(words) else None
        pause_after = (
            max(0.0, float(next_word["start"]) - float(word["end"]))
            if next_word
            else 0.0
        )
        punctuation_break = bool(re.search(r"[.!?,;:]$", str(word["text"])))

        should_break = len(current) >= max_words or (
            len(current) >= min_words and (punctuation_break or pause_after >= 0.35)
        )
        if should_break:
            grouped_words.append(current)
            current = []

    if current:
        grouped_words.append(current)

    # Avoid a one-word final flash by merging it or borrowing one word from a
    # full preceding group. A one-word transcript is the only unavoidable case.
    if len(grouped_words) > 1 and len(grouped_words[-1]) < min_words:
        previous = grouped_words[-2]
        final = grouped_words[-1]
        if len(previous) + len(final) <= max_words:
            previous.extend(final)
            grouped_words.pop()
        else:
            final.insert(0, previous.pop())

    groups = []
    uppercase_captions = os.getenv("CAPTION_UPPERCASE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    for group in grouped_words:
        caption_text = " ".join(str(word["text"]).strip() for word in group)
        caption_text = re.sub(r"[,.]+$", "", caption_text.strip())
        if uppercase_captions:
            caption_text = caption_text.upper()
        groups.append(
            {
                "text": caption_text,
                "start": max(0.0, float(group[0]["start"])),
                "end": max(float(group[0]["start"]) + 0.05, float(group[-1]["end"])),
            }
        )
    return groups


def _load_caption_font(size: int):
    """Load the bundled Montserrat font at ExtraBold weight."""
    try:
        font = ImageFont.truetype(str(CAPTION_FONT_PATH), size=size)
        try:
            font.set_variation_by_name("ExtraBold")
        except (AttributeError, OSError, ValueError):
            pass
        return font
    except OSError as exc:
        logger.warning("[Captions] Bundled font unavailable: %s", exc)
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
        except OSError:
            return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, stroke_width: int) -> str:
    """Wrap text to the requested pixel width without external text tools."""
    lines = []
    current = []
    for word in text.split():
        candidate = " ".join(current + [word])
        bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=font,
            stroke_width=stroke_width,
        )
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def render_text_overlay(
    text: str,
    max_width: int,
    font_size: int,
    min_font_size: int,
    stroke_width: int,
    padding: int = 18,
) -> Image.Image:
    """Render centered white text with a black stroke onto a compact RGBA image."""
    text = clean_text(text)
    measurement_canvas = Image.new("RGBA", (max_width, 800), (0, 0, 0, 0))
    draw = ImageDraw.Draw(measurement_canvas)
    inner_width = max(1, max_width - (padding * 2))

    chosen_font = None
    wrapped_text = text
    bbox = (0, 0, inner_width, font_size)
    for size in range(font_size, min_font_size - 1, -4):
        chosen_font = _load_caption_font(size)
        wrapped_text = _wrap_text(draw, text, chosen_font, inner_width, stroke_width)
        bbox = draw.multiline_textbbox(
            (0, 0),
            wrapped_text,
            font=chosen_font,
            spacing=8,
            align="center",
            stroke_width=stroke_width,
        )
        if bbox[2] - bbox[0] <= inner_width:
            break

    image_width = int(min(max_width, max(1, math.ceil(bbox[2] - bbox[0] + (padding * 2)))))
    image_height = int(max(1, math.ceil(bbox[3] - bbox[1] + (padding * 2))))
    image = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    image_draw = ImageDraw.Draw(image)
    image_draw.multiline_text(
        (image_width / 2, padding - bbox[1]),
        wrapped_text,
        font=chosen_font,
        fill=(255, 255, 255, 255),
        anchor="ma",
        align="center",
        spacing=8,
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255),
    )
    return image


def _caption_pop_scale(t: float) -> float:
    """Scale a caption from 90% to 100% over its first 100ms."""
    progress = min(1.0, max(0.0, t) / CAPTION_POP_DURATION)
    return 0.9 + (0.1 * progress)


def create_caption_clips(caption_groups: list) -> list:
    """Create transparent PIL caption clips positioned in the safe lower third."""
    clips = []
    max_width = VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2)
    for group in caption_groups:
        duration = max(0.05, float(group["end"]) - float(group["start"]))
        image = render_text_overlay(
            group["text"],
            max_width=max_width,
            font_size=CAPTION_FONT_SIZE,
            min_font_size=CAPTION_MIN_FONT_SIZE,
            stroke_width=CAPTION_STROKE_WIDTH,
        )
        top = max(0, CAPTION_SAFE_BOTTOM - image.height)
        clip = (
            ImageClip(np.array(image), transparent=True)
            .set_start(float(group["start"]))
            .set_duration(duration)
            .set_position(("center", top))
            .fx(vfx.resize, _caption_pop_scale)
        )
        clips.append(clip)
    return clips


def create_headline_clip(title: str, duration: float):
    """Create the static article headline shown during the opening hook."""
    headline = clean_text(title)
    if not headline or duration <= 0:
        return None
    if len(headline) > 140:
        headline = headline[:137].rsplit(" ", 1)[0] + "..."

    image = render_text_overlay(
        headline,
        max_width=VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2),
        font_size=76,
        min_font_size=44,
        stroke_width=7,
        padding=22,
    )
    return (
        ImageClip(np.array(image), transparent=True)
        .set_start(0)
        .set_duration(duration)
        .set_position(("center", 230))
    )


def chunk_text(text: str, words_per_chunk: int = DEFAULT_WORDS_PER_CHUNK) -> list:
    """Break text into chunks for visual pacing."""
    words = clean_text(text).split()
    return [" ".join(words[i:i + words_per_chunk]) for i in range(0, len(words), words_per_chunk) if words[i:i + words_per_chunk]]


TIKTOK_STYLES = """
STYLE A - 3D PIXAR/CGI:
Keywords: 3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant

STYLE B - VIBRANT PHOTOGRAPHY:
Keywords: Professional photography, bright natural lighting, saturated colors, high contrast, vivid

STYLE C - BOLD FLAT ILLUSTRATION:
Keywords: Flat illustration, bold colors, modern design, clean lines, vibrant, graphic
"""


def get_groq_client():
    """Get Groq client if API key exists."""
    api_key = os.getenv("GROQ_API_KEY")
    return Groq(api_key=api_key) if api_key else None


def select_style_with_groq(title: str, script: str) -> str:
    """Select visual style using AI."""
    client = get_groq_client()
    if not client:
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"

    try:
        logger.info("[Style] Selecting style...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Select BRIGHT, VIBRANT styles. Respond with only keywords."},
                {"role": "user", "content": f"Pick style for:\nTITLE: {title}\nCONTENT: {script[:2000]}\n\n{TIKTOK_STYLES}\n\nRespond with ONLY the style keywords."}
            ],
            temperature=0.5,
            max_tokens=100
        )
        style = response.choices[0].message.content.strip().strip('"\'')
        if "bright" not in style.lower() and "vibrant" not in style.lower():
            style += ", bright, vibrant, eye-catching"
        logger.info(f"[Style] {style[:50]}...")
        return style
    except Exception as e:
        logger.info(f"[Style] Failed: {e}")
        return "3D render, CGI, Pixar-style, bright colors, clean, professional, vibrant"


def extract_story_subjects(title: str, script: str) -> dict:
    """Extract visual subjects from content."""
    client = get_groq_client()
    default = {"main_subject": title, "visual_keywords": [title.split()[0] if title else "scene"], "setting": "general"}

    if not client:
        return default

    try:
        logger.info("[Subjects] Extracting subjects...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "Extract visual subjects. Respond only with valid JSON."},
                {"role": "user", "content": f'Analyze:\nTITLE: {title}\nCONTENT: {script[:3000]}\n\nRespond with JSON: {{"main_subject": "3-5 words", "visual_keywords": ["5 items"], "setting": "location"}}'}
            ],
            temperature=0.3,
            max_tokens=300
        )
        text = response.choices[0].message.content.strip()
        if "```" in text:
            text = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
            text = text.group(1) if text else "{}"
        subjects = json.loads(text)
        logger.info(f"[Subjects] {subjects.get('main_subject', 'unknown')}")
        return subjects
    except Exception as e:
        logger.info(f"[Subjects] Failed: {e}")
        return default


def generate_image_prompts(title: str, script: str, num_prompts: int, style: str, subjects: dict) -> list:
    """Generate image prompts using AI."""
    client = get_groq_client()
    if not client:
        return None

    main_subject = subjects.get("main_subject", title)
    visual_keywords = subjects.get("visual_keywords", [])
    setting = subjects.get("setting", "")
    keywords_str = ", ".join(visual_keywords) if visual_keywords else title

    try:
        logger.info("[Prompts] Generating prompts...")
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": f"Generate prompts for '{main_subject}'. Include keywords: {keywords_str}. JSON array only."},
                {"role": "user", "content": f"Generate {num_prompts} image prompts.\nTITLE: {title}\nSETTING: {setting}\nSTYLE: {style}\nSCRIPT: {script[:4000]}\n\nRules: 15-30 words each, vertical 9:16, NO text in images.\nRespond with JSON array: [\"prompt1\", \"prompt2\", ...]"}
            ],
            temperature=0.7,
            max_tokens=2500
        )
        text = response.choices[0].message.content.strip()
        match = re.search(r"\[[\s\S]*\]", text)
        if match:
            prompts = json.loads(match.group(0))
            if isinstance(prompts, list) and len(prompts) >= num_prompts:
                logger.info(f"[Prompts] Generated {len(prompts)} prompts")
                return prompts[:num_prompts]
        return None
    except Exception as e:
        logger.info(f"[Prompts] Failed: {e}")
        return None


def generate_themed_images(title: str, script: str, num_images: int = NUM_BODY_IMAGES, image_source: str = "ai") -> list:
    """Generate themed images for video body.

    Args:
        image_source: 'ai' for FAL.ai generation, 'stock' for Pexels stock photos.
    """
    if image_source == "stock":
        logger.info(f"[Video] Fetching {num_images} stock photos from Pexels...")
        keywords = _extract_search_keywords(title, script)
        # Spread images across multiple search terms for variety
        images = []
        per_keyword = max(1, num_images // len(keywords))
        for kw in keywords:
            if len(images) >= num_images:
                break
            needed = min(per_keyword, num_images - len(images))
            images.extend(search_pexels_images(kw, needed))
        # Pad if not enough
        while len(images) < num_images:
            images.extend(search_pexels_images(title, num_images - len(images)))
        return images[:num_images]

    # Default: AI-generated images
    logger.info(f"[Video] Generating {num_images} AI images in parallel (max {MAX_IMAGE_WORKERS} workers)...")

    subjects = extract_story_subjects(title, script)
    style = select_style_with_groq(title, script)
    prompts = generate_image_prompts(title, script, num_images, style, subjects)

    if not prompts:
        logger.info("[Video] Using fallback prompts")
        keywords = subjects.get("visual_keywords", [title])
        setting = subjects.get("setting", "")
        prompts = [f"{kw}, {setting}, {style}" for kw in (keywords * 5)[:num_images]]

    # Parallel image generation
    images = [None] * len(prompts)
    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate_image_fal, prompt): i
            for i, prompt in enumerate(prompts)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                images[idx] = future.result()
            except Exception as e:
                logger.info(f"[Video] Image {idx+1} failed: {e}")
                images[idx] = create_gradient_background()

    logger.info(f"[Video] Generated {len(images)} images")
    return images


def create_hook_clips(title: str, duration: float = HOOK_DURATION, image_source: str = "ai") -> list:
    """Create rapid-fire hook sequence."""
    clip_duration = duration / NUM_HOOK_IMAGES

    if image_source == "stock":
        logger.info(f"[Hook] Fetching {NUM_HOOK_IMAGES} stock hook images...")
        images = search_pexels_images(title, NUM_HOOK_IMAGES)
    else:
        hook_prompts = [
            f"extreme macro close-up shot, {title}, ultra sharp detail, dramatic rim lighting, shallow depth of field, cinematic 9:16, hyper-realistic",
            f"impossible camera angle, {title}, bird's eye view mixed with dutch angle, dramatic shadows, high contrast neon accents, surreal perspective",
            f"frozen action moment, {title}, motion blur trails, dynamic energy, explosive composition, vibrant saturated colors, dramatic backlighting",
            f"bold graphic composition, {title}, stark contrast, complementary color explosion, minimalist but striking, professional advertising quality"
        ]
        logger.info(f"[Hook] Creating {NUM_HOOK_IMAGES} hook images in parallel...")
        images = [None] * NUM_HOOK_IMAGES
        with ThreadPoolExecutor(max_workers=NUM_HOOK_IMAGES) as executor:
            future_to_idx = {
                executor.submit(generate_image_fal, prompt): i
                for i, prompt in enumerate(hook_prompts[:NUM_HOOK_IMAGES])
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    images[idx] = future.result()
                except Exception:
                    images[idx] = create_gradient_background()

    clips = [create_clip(img, clip_duration, zoom_factor=0.05) for img in images]
    logger.info(f"[Hook] Created {len(clips)} clips ({clip_duration:.2f}s each)")
    return clips


def create_clip(image: Image.Image, duration: float, zoom_factor: float = 0.03) -> VideoClip:
    """Create a centered Ken Burns zoom while keeping every frame 1080x1920."""
    source = resize_and_crop_image(image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    duration = max(0.05, float(duration))

    def make_frame(t: float) -> np.ndarray:
        progress = max(0.0, min(1.0, float(t) / duration))
        scale = 1.0 + (max(0.0, zoom_factor) * progress)
        zoom_width = max(VIDEO_WIDTH, int(math.ceil(VIDEO_WIDTH * scale)))
        zoom_height = max(VIDEO_HEIGHT, int(math.ceil(VIDEO_HEIGHT * scale)))
        zoomed = source.resize((zoom_width, zoom_height), Image.Resampling.LANCZOS)
        left = (zoom_width - VIDEO_WIDTH) // 2
        top = (zoom_height - VIDEO_HEIGHT) // 2
        cropped = zoomed.crop(
            (left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT)
        )
        return np.asarray(cropped, dtype=np.uint8)

    return VideoClip(make_frame=make_frame, duration=duration)



def compute_durations(chunks: list, total_time: float) -> list:
    """Allocate time per chunk based on word count."""
    if not chunks:
        return []

    raw = [max(MIN_CHUNK_DURATION, min(MAX_CHUNK_DURATION, 0.45 * max(1, len(c.split())))) for c in chunks]
    raw_sum = sum(raw)

    if raw_sum <= 0:
        return [DEFAULT_CHUNK_DURATION] * len(chunks)

    scale = total_time / raw_sum
    durations = [d * scale for d in raw]
    durations[-1] += total_time - sum(durations)  # Fix drift
    return [max(0.05, d) for d in durations]


def generate_video(
    article_id: int,
    title: str,
    script: str,
    image_source: str = "ai",
    captions: bool = True,
) -> str:
    """Generate TikTok-style video with parallel image generation.

    Args:
        image_source: 'ai' for FAL.ai, 'stock' for Pexels stock photos.
        captions: Burn word-synced captions into the video when True.
    """
    videos_dir = Path("static/videos")
    videos_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = videos_dir / f"article_{article_id}_{timestamp}.mp4"
    temp_audio_path = videos_dir / f"temp_audio_{article_id}.mp3"

    audio = None
    main_video = None
    base_video = None
    clips = []
    overlay_clips = []
    actual_audio_path = None

    try:
        logger.info(f"Generating video for article {article_id} (images: {image_source})")

        # Step 1: TTS
        logger.info("Step 1: Generating voiceover...")
        narration_text = clean_text(script)
        actual_audio_path = tts_engine.synthesize(narration_text, str(temp_audio_path))
        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        logger.info(f"Audio duration: {audio_duration:.1f}s")

        # Step 2: Word-level caption timings
        caption_groups = []
        if captions:
            logger.info("Step 2: Transcribing word-synced captions...")
            timed_words = transcribe_word_timestamps(
                actual_audio_path,
                script_text=narration_text,
            )
            caption_groups = group_words_for_captions(timed_words)
            logger.info("[Captions] Created %d caption groups", len(caption_groups))

        hook_len = min(HOOK_DURATION, max(2.0, audio_duration * 0.25))

        # Step 3: Generate images
        logger.info("Step 3: Generating images...")
        themed_images = generate_themed_images(title, script, num_images=NUM_BODY_IMAGES, image_source=image_source)

        # Step 4: Chunk for pacing
        logger.info("Step 4: Chunking script...")
        chunks = chunk_text(script)
        logger.info(f"{len(chunks)} chunks")

        # Step 5: Hook clips (rapid-fire image sequence)
        logger.info("Step 5: Creating hook sequence...")
        hook_clips = create_hook_clips(title, duration=hook_len, image_source=image_source)
        clips.extend(hook_clips)
        logger.info(f"Hook: {hook_len:.1f}s")

        # Step 6: Body clips
        logger.info("Step 6: Creating body clips...")
        remaining = max(0.1, audio_duration - hook_len)
        durations = compute_durations(chunks, remaining)

        for i in range(len(chunks)):
            img = themed_images[i % len(themed_images)]
            dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
            clips.append(create_clip(img, dur))

        # Step 7: Assemble visuals and PIL text overlays
        logger.info("Step 7: Assembling...")
        main_video = concatenate_videoclips(clips, method="compose")

        if main_video.duration > audio_duration:
            main_video = main_video.subclip(0, audio_duration)
        elif main_video.duration < audio_duration:
            pad = audio_duration - main_video.duration
            if pad < (1.0 / FPS):
                main_video = main_video.set_duration(audio_duration)
            else:
                last_frame_time = max(0.0, main_video.duration - (1.0 / FPS))
                last_hold = main_video.to_ImageClip(t=last_frame_time).set_duration(pad)
                main_video = concatenate_videoclips(
                    [main_video, last_hold],
                    method="compose",
                )

        headline_clip = create_headline_clip(title, min(HEADLINE_DURATION, audio_duration))
        if headline_clip:
            overlay_clips.append(headline_clip)
        if captions and caption_groups:
            overlay_clips.extend(create_caption_clips(caption_groups))

        if overlay_clips:
            base_video = main_video
            main_video = CompositeVideoClip(
                [base_video, *overlay_clips],
                size=(VIDEO_WIDTH, VIDEO_HEIGHT),
            ).set_duration(audio_duration)

        main_video = main_video.set_audio(audio)
        logger.info(f"Final duration: {main_video.duration:.1f}s")

        # Step 8: Render
        logger.info("Step 8: Rendering...")
        try:
            crf = int(os.getenv("VIDEO_CRF", "26"))
            if not 0 <= crf <= 51:
                raise ValueError
        except ValueError:
            crf = 26
            logger.warning("Invalid VIDEO_CRF; using 26")
        main_video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            audio_bitrate="128k",
            threads=4,
            preset="veryfast",
            ffmpeg_params=["-crf", str(crf), "-pix_fmt", "yuv420p"],
            verbose=False,
            logger=None
        )

        logger.info(f"Video saved: {output_path}")
        return str(output_path)

    except Exception as e:
        logger.error(f"Video generation failed: {e}", exc_info=True)
        raise

    finally:
        for resource in [audio, main_video, base_video]:
            try:
                if resource:
                    resource.close()
            except Exception:
                pass
        for overlay in overlay_clips:
            try:
                overlay.close()
            except Exception:
                pass
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        if actual_audio_path:
            try:
                Path(actual_audio_path).unlink(missing_ok=True)
            except OSError:
                pass

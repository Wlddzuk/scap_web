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
import tempfile
import threading
from collections import Counter
from datetime import datetime
from pathlib import Path
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from groq import Groq
import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from moviepy.audio.fx import all as afx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat
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

# AI video hook. When the per-request `use_video_hook` flag is True (set by the
# UI toggle), or when HOOK_VIDEO_MODEL is set in the env, the 5s hook becomes a
# single FAL video clip instead of 4 stills with Ken Burns zoom. Falls back to
# image hook on any failure.
# Models tested: fal-ai/ltx-video (cheap/fast), fal-ai/kling-video/v1/standard/text-to-video,
# fal-ai/minimax/video-01.
HOOK_VIDEO_MODEL = os.getenv("HOOK_VIDEO_MODEL", "").strip()
HOOK_VIDEO_ASPECT = os.getenv("HOOK_VIDEO_ASPECT", "9:16")
DEFAULT_HOOK_VIDEO_MODEL = "fal-ai/ltx-video"  # used when UI toggle is on and env is empty


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Read a bounded integer setting without letting bad env values break startup."""
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _bounded_float_env(
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    """Read a bounded float setting without letting bad env values break startup."""
    try:
        return max(minimum, min(maximum, float(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


# This cap applies to every generated motion clip, including the hook. Keeping
# the default at three holds the estimated motion spend below the work-order's
# $0.60/video ceiling while still allowing two body scenes after a video hook.
_REQUESTED_MAX_VIDEO_CLIPS_PER_VIDEO = _bounded_int_env(
    "MAX_VIDEO_CLIPS_PER_VIDEO", 3, 0, 12
)
FAL_VIDEO_TIMEOUT_SECONDS = _bounded_int_env(
    "FAL_VIDEO_TIMEOUT_SECONDS", 180, 30, 900
)
FAL_IMAGE_TIMEOUT_SECONDS = _bounded_int_env(
    "FAL_IMAGE_TIMEOUT_SECONDS", 120, 30, 900
)
FAL_IMAGE_MODEL = (
    os.getenv("FAL_IMAGE_MODEL", "fal-ai/flux/schnell").strip()
    or "fal-ai/flux/schnell"
)
FAL_IMAGE_STEPS = _bounded_int_env("FAL_IMAGE_STEPS", 4, 1, 100)
FAL_HOOK_IMAGE_MODEL = (
    os.getenv("FAL_HOOK_IMAGE_MODEL", "fal-ai/flux/dev").strip()
    or "fal-ai/flux/dev"
)
# Official default-model prices checked at implementation time. portrait_16_9
# is 576x1024 and therefore rounds up to one billed megapixel. Operators using
# a different model can override each price without falsifying the budget UI.
FAL_IMAGE_COST_USD = _bounded_float_env(
    "FAL_IMAGE_COST_PER_MP_USD", 0.003, 0.0, 100.0
)
FAL_HOOK_IMAGE_COST_USD = _bounded_float_env(
    "FAL_HOOK_IMAGE_COST_PER_MP_USD", 0.025, 0.0, 100.0
)
try:
    VIDEO_CLIP_ESTIMATED_COST_USD = max(
        0.0, float(os.getenv("VIDEO_CLIP_ESTIMATED_COST_USD", "0.18"))
    )
except (TypeError, ValueError):
    VIDEO_CLIP_ESTIMATED_COST_USD = 0.18

# The automatic Illustrated Science render uses one premium hook illustration,
# one illustration for each of the first two scenes, and about ten cheap later
# scenes. Each scene image is reused across its progressive teaching shots.
# generate_video computes the exact plan before allowing optional motion.
BASE_VIDEO_ESTIMATED_COST_USD = (
    3 * FAL_HOOK_IMAGE_COST_USD
    + 10 * FAL_IMAGE_COST_USD
)
MAX_VIDEO_ESTIMATED_COST_USD = _bounded_float_env(
    "MAX_VIDEO_ESTIMATED_COST_USD", 0.60, 0.0, 1000.0
)
ABSOLUTE_MAX_VIDEO_CLIPS_PER_VIDEO = 3


def _effective_motion_clip_cap(
    requested: int,
    estimated_cost_per_clip: float,
    base_cost: float = BASE_VIDEO_ESTIMATED_COST_USD,
    max_total_cost: float = MAX_VIDEO_ESTIMATED_COST_USD,
) -> int:
    """Enforce both the three-clip cap and the configured dollar ceiling."""
    count_cap = min(
        ABSOLUTE_MAX_VIDEO_CLIPS_PER_VIDEO,
        max(0, int(requested)),
    )
    if estimated_cost_per_clip <= 0:
        return count_cap
    remaining_budget = max(0.0, float(max_total_cost) - float(base_cost))
    budget_cap = int(math.floor((remaining_budget + 1e-9) / estimated_cost_per_clip))
    return min(count_cap, max(0, budget_cap))


MAX_VIDEO_CLIPS_PER_VIDEO = _effective_motion_clip_cap(
    _REQUESTED_MAX_VIDEO_CLIPS_PER_VIDEO,
    VIDEO_CLIP_ESTIMATED_COST_USD,
)

# Body image count (reduced from 20 for speed)
NUM_BODY_IMAGES = 14

# Parallel image generation workers
MAX_IMAGE_WORKERS = 6

# An archive image is treated as a scanned diagram when it is bimodal "ink on a
# page": mostly near-white with very little mid-tone. Measured against real
# sources -- the NASA Gemini schematic scores white 0.59 / mid 0.11, while photos
# including a bright snowy landscape stay above mid 0.54.
DOCUMENTARY_PAGE_WHITE_SHARE = float(
    os.getenv("DOCUMENTARY_PAGE_WHITE_SHARE", "0.35") or 0.35
)
DOCUMENTARY_PAGE_MID_SHARE = float(
    os.getenv("DOCUMENTARY_PAGE_MID_SHARE", "0.35") or 0.35
)

# Timing constraints
MIN_CHUNK_DURATION = 1.2
MAX_CHUNK_DURATION = 3.2
DEFAULT_CHUNK_DURATION = 2.5
MAX_SHOT_DURATION = 2.5
DEFAULT_WORDS_PER_CHUNK = 4
RETRY_ATTEMPTS = 2
SHOT_TYPES = (
    "macro close-up",
    "wide establishing shot",
    "human-scale perspective",
    "detail with scale contrast",
)
SHOT_MOTIONS = ("push", "pan-left", "pan-right", "pull")

# Color is graded once per still before MoviePy starts animating it. This keeps
# the look deterministic across AI, stock, hero, and NASA imagery without
# paying for a per-frame filter throughout a 60-second render.
DEFAULT_COLOR_INTENSITY = "vivid"
COLOR_INTENSITY_PRESETS = {
    "natural": {
        "label": "Natural",
        "description": "True-to-life color with no added saturation or contrast.",
        "saturation": 1.0,
        "contrast": 1.0,
        "prompt_guidance": (
            "natural true-to-life color, neutral white balance, restrained "
            "saturation, realistic highlights"
        ),
    },
    "vivid": {
        "label": "Vivid",
        "description": "Richer color and crisp contrast while staying believable.",
        "saturation": 1.30,
        "contrast": 1.10,
        "prompt_guidance": (
            "vivid yet believable color, rich saturation, crisp contrast, "
            "protected skin tones and highlights"
        ),
    },
    "electric": {
        "label": "Electric",
        "description": "Extreme high-chroma cyan, magenta, blue, and red.",
        "saturation": 1.72,
        "contrast": 1.18,
        "prompt_guidance": (
            "electric high-chroma color, intense cyan, cobalt blue, hot "
            "magenta and vivid red accents, deep blacks, luminous highlights "
            "without clipping"
        ),
    },
}


def normalize_color_intensity(color_intensity: str | None) -> str:
    """Return a stable color preset id, falling back to Vivid."""
    key = str(color_intensity or DEFAULT_COLOR_INTENSITY).strip().lower()
    if key not in COLOR_INTENSITY_PRESETS:
        logger.warning(
            "[Color] Unknown intensity %s; using %s",
            color_intensity,
            DEFAULT_COLOR_INTENSITY,
        )
        return DEFAULT_COLOR_INTENSITY
    return key


def color_intensity_options() -> list[dict[str, str]]:
    """Return browser-safe preset metadata in display order."""
    return [
        {
            "id": key,
            "label": str(preset["label"]),
            "description": str(preset["description"]),
        }
        for key, preset in COLOR_INTENSITY_PRESETS.items()
    ]


def color_intensity_prompt_guidance(color_intensity: str | None) -> str:
    """Return deterministic palette guidance for still and motion prompts."""
    key = normalize_color_intensity(color_intensity)
    return str(COLOR_INTENSITY_PRESETS[key]["prompt_guidance"])


def apply_color_intensity(
    image: Image.Image,
    color_intensity: str | None = DEFAULT_COLOR_INTENSITY,
) -> Image.Image:
    """Apply one preset to a still without introducing a frame-time filter.

    Natural deliberately returns the exact source object. Vivid and Electric
    use Pillow's luminance-aware color enhancement, followed by a modest
    contrast curve. Saturation changes therefore preserve tonal detail better
    than multiplying RGB channels and Electric remains visibly stronger than
    Vivid without raising brightness or clipping highlights wholesale.
    """
    key = normalize_color_intensity(color_intensity)
    if key == "natural" or not isinstance(image, Image.Image):
        return image

    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    source = image.convert("RGB")
    preset = COLOR_INTENSITY_PRESETS[key]
    graded = ImageEnhance.Color(source).enhance(float(preset["saturation"]))
    graded = ImageEnhance.Contrast(graded).enhance(float(preset["contrast"]))
    if alpha is not None:
        graded = graded.convert("RGBA")
        graded.putalpha(alpha)
    return graded


def apply_color_intensity_to_images(
    images: list,
    color_intensity: str | None = DEFAULT_COLOR_INTENSITY,
) -> list:
    """Grade a list of stills once while preserving its order."""
    key = normalize_color_intensity(color_intensity)
    return [apply_color_intensity(image, key) for image in images]


def estimate_ai_still_cost(premium_images: int, body_images: int) -> float:
    """Estimate FAL still spend for the configured premium/body split."""
    premium = max(0, int(premium_images))
    body = max(0, int(body_images))
    return (
        premium * FAL_HOOK_IMAGE_COST_USD
        + body * FAL_IMAGE_COST_USD
    )


def estimate_planned_still_cost(
    body_shots: list,
    *,
    use_scenes: bool,
    image_source: str,
    style_key: str | None = None,
) -> float:
    """Conservatively price the still fallbacks for one planned render.

    A motion request can always fail back to four premium hook stills. Scene
    and mixed-source renders can likewise fall back to AI in every body slot,
    so real imagery is deliberately not deducted before those requests finish.
    Legacy AI renders generate a fixed pool of cheap body images which is
    reused across the shot plan.
    """
    source = str(image_source or "ai").strip().lower()
    if use_scenes:
        # Real-image searches are free, but any unique scene may need a
        # premium, deliberately illustrated fallback. Price the worst case so
        # optional motion can never silently push the render over budget.
        unique_scene_ids = set()
        for index, shot in enumerate(body_shots or []):
            try:
                scene_index = int((shot or {}).get("_scene_index", index))
            except (TypeError, ValueError):
                scene_index = index
            unique_scene_ids.add(scene_index)
        return estimate_ai_still_cost(len(unique_scene_ids), 0)
    if source == "stock":
        return 0.0

    if not use_scenes and source == "ai":
        return estimate_ai_still_cost(NUM_HOOK_IMAGES, NUM_BODY_IMAGES)

    premium_body = 0
    cheap_body = 0
    for index, shot in enumerate(body_shots or []):
        try:
            scene_index = int((shot or {}).get("_scene_index", index))
        except (TypeError, ValueError):
            scene_index = index
        if scene_index < 2:
            premium_body += 1
        else:
            cheap_body += 1

    return estimate_ai_still_cost(
        NUM_HOOK_IMAGES + premium_body,
        cheap_body,
    )

# Caption settings
CAPTION_FONT_PATH = Path(__file__).resolve().parent / "static" / "fonts" / "Montserrat-Variable.ttf"
CAPTION_FONT_SIZE = 88
CAPTION_MIN_FONT_SIZE = 56
CAPTION_STROKE_WIDTH = 7
CAPTION_SIDE_MARGIN = 80
CAPTION_SAFE_BOTTOM = 1500
CAPTION_POP_DURATION = 0.1
CAPTION_ACTIVE_COLOR = (255, 216, 77, 255)
HEADLINE_DURATION = 2.5
WHISPER_MODELS = {"tiny", "base"}

# Audio settings. Each bundled track is peak-normalized at mix time before these
# target gains are applied, while the narration gain reserves summing headroom.
MUSIC_DIR = Path(__file__).resolve().parent / "static" / "audio" / "music"
MUSIC_DUCKED_DB = -22.0
MUSIC_GAP_DB = -12.0
MUSIC_FADE_IN_SECONDS = 0.5
MUSIC_FADE_OUT_SECONDS = 1.0
MUSIC_DUCK_ATTACK_SECONDS = 0.08
MUSIC_DUCK_RELEASE_SECONDS = 0.18
NARRATION_MIX_GAIN = 0.74

_WHISPER_MODEL = None
_WHISPER_MODEL_NAME = None
_WHISPER_LOCK = threading.Lock()

# Pillow 10+ compatibility
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS


def generate_image_fal(
    prompt: str,
    retry_count: int = RETRY_ATTEMPTS,
    *,
    model: str | None = None,
    num_inference_steps: int | None = FAL_IMAGE_STEPS,
) -> Image.Image:
    """Generate image using FAL.ai FLUX model."""
    import fal_client

    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        logger.info("[Image] No FAL_KEY, using gradient")
        return create_gradient_background()

    enhanced_prompt = (
        f"{prompt}, clear high-contrast focal hierarchy, faithful to the requested "
        f"medium and palette, clean composition, vertical 9:16, professional quality, "
        f"no text no words"
    )

    selected_model = model or FAL_IMAGE_MODEL
    image_url = None
    for attempt in range(retry_count):
        try:
            arguments = {
                "prompt": enhanced_prompt,
                "image_size": "portrait_16_9",
                "num_images": 1,
            }
            # FLUX dev's official default is 28. Hook callers deliberately pass
            # None so the premium model is not accidentally reduced to 4 steps.
            if num_inference_steps is not None:
                arguments["num_inference_steps"] = num_inference_steps
            logger.info(
                "[Image] Generating via %s: %s...",
                selected_model,
                prompt[:40],
            )
            result = fal_client.run(
                selected_model,
                arguments=arguments,
                timeout=FAL_IMAGE_TIMEOUT_SECONDS,
                start_timeout=min(30, FAL_IMAGE_TIMEOUT_SECONDS),
            )

            if result and "images" in result and result["images"]:
                image_url = result["images"][0].get("url")
            if not image_url:
                raise ValueError("FAL image response did not include a URL")
            break

        except Exception as e:
            logger.info(f"[Image] Inference attempt {attempt + 1} failed: {e}")
            if attempt + 1 < retry_count:
                time.sleep(2)

    if image_url:
        # Once inference succeeds, never repeat the paid model call just because
        # the provider CDN is briefly unavailable. Retry only the free download.
        for attempt in range(retry_count):
            try:
                response = requests.get(image_url, timeout=30)
                response.raise_for_status()
                img = Image.open(BytesIO(response.content)).convert("RGB")
                img = resize_and_crop_image(img, VIDEO_WIDTH, VIDEO_HEIGHT)
                logger.info("[Image] Generated")
                return img
            except Exception as e:
                logger.info(
                    f"[Image] Download attempt {attempt + 1} failed: {e}"
                )
                if attempt + 1 < retry_count:
                    time.sleep(2)

    logger.info("[Image] Failed, using gradient")
    return create_gradient_background()


def generate_motion_video_fal(
    prompt: str,
    model: str,
    log_label: str = "MotionVideo",
) -> str | None:
    """Generate a short AI motion clip via FAL, with a bounded wait.

    Caller is responsible for unlinking the returned path. We write to the system
    temp dir (not static/) so failures don't leak public files.
    """
    import fal_client
    import tempfile

    if not os.getenv("FAL_KEY"):
        logger.info(f"[{log_label}] No FAL_KEY, skipping motion clip")
        return None

    local_path = None
    try:
        logger.info(f"[{log_label}] Generating via {model}: {prompt[:80]}...")
        result = fal_client.run(
            model,
            arguments={"prompt": prompt, "aspect_ratio": HOOK_VIDEO_ASPECT},
            timeout=FAL_VIDEO_TIMEOUT_SECONDS,
            start_timeout=min(30, FAL_VIDEO_TIMEOUT_SECONDS),
        )

        video_url = None
        if isinstance(result, dict):
            video_field = result.get("video")
            if isinstance(video_field, dict):
                video_url = video_field.get("url")
            elif isinstance(video_field, str):
                video_url = video_field
            elif "url" in result:
                video_url = result["url"]

        if not video_url:
            logger.info(
                f"[{log_label}] No video URL in response keys="
                f"{list(result) if isinstance(result, dict) else type(result)}"
            )
            return None

        fd, local_path = tempfile.mkstemp(suffix=".mp4", prefix="clipper_motion_")
        os.close(fd)
        response = requests.get(video_url, timeout=120, stream=True)
        response.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)

        logger.info(f"[{log_label}] Saved: {local_path}")
        return local_path
    except Exception as e:
        logger.info(f"[{log_label}] Failed: {e}")
        if local_path:
            try:
                Path(local_path).unlink(missing_ok=True)
            except OSError:
                pass
        return None


def generate_hook_video_fal(prompt: str, model: str) -> str | None:
    """Backward-compatible hook wrapper around the shared motion generator."""
    return generate_motion_video_fal(prompt, model, log_label="HookVideo")


def load_hook_video_clip(video_path: str, target_duration: float):
    """Load a downloaded FAL video as a moviepy clip fitted to 1080x1920 / target_duration."""
    clip = VideoFileClip(video_path).without_audio()

    if clip.duration > target_duration:
        clip = clip.subclip(0, target_duration)
    elif clip.duration < target_duration:
        # If close, ease via speed; otherwise loop.
        if clip.duration >= target_duration * 0.7:
            clip = clip.fx(vfx.speedx, clip.duration / target_duration)
        else:
            loops = int(target_duration / clip.duration) + 1
            clip = concatenate_videoclips([clip] * loops, method="compose").subclip(0, target_duration)

    cw, ch = clip.size
    target_ratio = VIDEO_WIDTH / VIDEO_HEIGHT
    src_ratio = cw / ch

    if abs(src_ratio - target_ratio) > 0.01:
        if src_ratio > target_ratio:
            new_w = int(ch * target_ratio)
            x_center = cw // 2
            clip = clip.crop(x1=x_center - new_w // 2, x2=x_center + new_w // 2)
        else:
            new_h = int(cw / target_ratio)
            y_center = ch // 2
            clip = clip.crop(y1=y_center - new_h // 2, y2=y_center + new_h // 2)

    return clip.resize((VIDEO_WIDTH, VIDEO_HEIGHT))


def split_motion_clip(clip, duration: float) -> list:
    """Slice a motion clip into edit shots no longer than the pacing cap."""
    durations = split_shot_duration(duration)
    if len(durations) <= 1:
        return [clip]

    slices = []
    start = 0.0
    for shot_duration in durations:
        end = min(duration, start + shot_duration)
        slices.append(clip.subclip(start, end))
        start = end
    # Keep the original reader alive through render and close it during cleanup.
    slices[0]._scap_parent_clip = clip
    return slices


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


_CAPTION_WEAK_END_WORDS = {
    "a", "about", "above", "across", "after", "against", "along", "among",
    "an", "around", "as", "at", "before", "behind", "below", "beneath",
    "beside", "between", "beyond", "by", "despite", "down", "during",
    "except", "for", "from", "in", "inside", "into", "like", "near", "of",
    "off", "on", "onto", "out", "outside", "over", "past", "since", "the",
    "through", "throughout", "to", "toward", "under", "until", "up", "upon",
    "with", "within", "without",
}
_CAPTION_NUMBER_MAGNITUDES = {
    "hundred", "thousand", "million", "billion", "trillion", "quadrillion",
}
_CAPTION_COMPOUND_UNIT_PREFIXES = {"light", "square", "cubic"}
_CAPTION_MEASUREMENT_UNITS = {
    "%", "percent", "percentage", "second", "seconds", "minute", "minutes", "hour", "hours",
    "day", "days", "week", "weeks", "month", "months", "year", "years",
    "light-year", "light-years",
    "meter", "meters", "metre", "metres", "kilometer", "kilometers",
    "kilometre", "kilometres", "mile", "miles", "gram", "grams", "kilogram",
    "kilograms", "ton", "tons", "tonne", "tonnes", "degree", "degrees",
    "celsius", "fahrenheit", "kelvin", "byte", "bytes", "kilobyte", "kilobytes",
    "megabyte", "megabytes", "gigabyte", "gigabytes", "terabyte", "terabytes",
    "watt", "watts", "volt", "volts", "hertz", "hz", "khz", "mhz", "ghz",
    "mm", "cm", "m", "km", "mg", "g", "kg", "mph", "kph", "°c", "°f",
}
_CAPTION_NUMBER_UNITS = (
    _CAPTION_NUMBER_MAGNITUDES
    | _CAPTION_COMPOUND_UNIT_PREFIXES
    | _CAPTION_MEASUREMENT_UNITS
)


def _caption_token_key(text: str) -> str:
    """Normalize a spoken token for phrase-boundary decisions."""
    return str(text).strip().strip("\"'“”‘’()[]{}.,!?;:").lower()


def _caption_token_is_number(text: str) -> bool:
    token = _caption_token_key(text).replace(",", "")
    return bool(
        re.fullmatch(
            r"(?:about|over|under|nearly)?[~≈<>+\-]?[$£€]?\d+(?:\.\d+)?"
            r"(?:e[+\-]?\d+)?(?:%|°[cf]?)?",
            token,
            flags=re.IGNORECASE,
        )
    )


def _caption_boundary_allowed(words: list, boundary: int) -> bool:
    """Return whether a cue may end immediately before ``boundary``."""
    if boundary <= 0 or boundary >= len(words):
        return True
    left = _caption_token_key(words[boundary - 1].get("text", ""))
    right = _caption_token_key(words[boundary].get("text", ""))
    if left in _CAPTION_WEAK_END_WORDS:
        return False
    if _caption_token_is_number(left) and right in _CAPTION_NUMBER_UNITS:
        return False
    # Keep a complete quantity together, not just its first two tokens. Without
    # this, "11 billion years" merely moves the bad break from 11|billion to
    # billion|years. Punctuation on the magnitude still permits a natural break
    # for standalone values such as "the estimate was 11 billion, but ...".
    if left in _CAPTION_NUMBER_MAGNITUDES:
        left_source = str(words[boundary - 1].get("text", "")).strip()
        previous = (
            _caption_token_key(words[boundary - 2].get("text", ""))
            if boundary >= 2
            else ""
        )
        if (
            (_caption_token_is_number(previous) or previous in _CAPTION_NUMBER_MAGNITUDES)
            and not re.search(r"[,.!?;:]$", left_source)
        ):
            return False
    if left in _CAPTION_COMPOUND_UNIT_PREFIXES and right in _CAPTION_MEASUREMENT_UNITS:
        lookback = [
            _caption_token_key(word.get("text", ""))
            for word in words[max(0, boundary - 4):boundary - 1]
        ]
        if any(_caption_token_is_number(token) for token in lookback):
            return False
    return True


def _caption_group_ranges(words: list, min_words: int, max_words: int) -> list:
    """Choose phrase-safe cue ranges using a small global optimization.

    The dynamic program avoids fixing one boundary only to create a one-word
    flash later. Normal cues remain 2--4 words, while a rare five-word cue is
    preferred over breaking a number/unit pair or ending on a preposition.
    """
    count = len(words)
    if not count:
        return []

    # dp[end] = (cost, preceding boundary list)
    dp = [(float("inf"), None)] * (count + 1)
    dp[0] = (0.0, [])

    for end in range(1, count + 1):
        for start in range(0, end):
            previous_cost, previous_ranges = dp[start]
            if previous_ranges is None or not _caption_boundary_allowed(words, start):
                continue
            if end < count and not _caption_boundary_allowed(words, end):
                continue

            length = end - start
            if min_words <= length <= max_words:
                length_cost = {2: 0.35, 3: 0.0, 4: 0.2}.get(length, 0.2)
            elif length == 1:
                length_cost = 9.0
            else:
                length_cost = 4.0 + (abs(length - max_words) * 2.0)

            # A modest per-cue cost avoids over-fragmenting everything into
            # two-word flashes. Pauses/punctuation remain preferred boundaries.
            cost = previous_cost + 1.0 + length_cost
            if end < count:
                current = words[end - 1]
                following = words[end]
                pause = max(
                    0.0,
                    float(following.get("start", 0.0)) - float(current.get("end", 0.0)),
                )
                if re.search(r"[.!?,;:]$", str(current.get("text", ""))):
                    cost -= 0.8
                elif pause >= 0.35:
                    cost -= 0.55

            if cost < dp[end][0]:
                dp[end] = (cost, [*previous_ranges, (start, end)])

    ranges = dp[count][1]
    return ranges if ranges is not None else [(0, count)]


def group_words_for_captions(words: list, min_words: int = 2, max_words: int = 4) -> list:
    """Group timed words into phrase-safe, karaoke-ready caption cues."""
    if not words:
        return []
    min_words = max(1, int(min_words))
    max_words = max(min_words, int(max_words))
    uppercase_captions = os.getenv("CAPTION_UPPERCASE", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }

    groups = []
    for start_index, end_index in _caption_group_ranges(words, min_words, max_words):
        source_words = words[start_index:end_index]
        display_words = []
        for source in source_words:
            text = str(source.get("text", "")).strip()
            if uppercase_captions:
                text = text.upper()
            display_words.append(
                {
                    "text": text,
                    "start": max(0.0, float(source.get("start", 0.0))),
                    "end": max(
                        max(0.0, float(source.get("start", 0.0))) + 0.05,
                        float(source.get("end", 0.0)),
                    ),
                }
            )

        # Caption builds now use one punctuation rule: sentence punctuation is
        # removed only at the end of a cue, while interior punctuation remains.
        display_words[-1]["text"] = re.sub(
            r"[,.!?;:]+$", "", display_words[-1]["text"]
        )
        caption_text = " ".join(word["text"] for word in display_words).strip()
        groups.append(
            {
                "text": caption_text,
                "start": display_words[0]["start"],
                "end": max(display_words[0]["start"] + 0.05, display_words[-1]["end"]),
                "words": display_words,
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
    max_lines: int | None = None,
) -> Image.Image:
    """Render centered white text with a black stroke onto a compact RGBA image."""
    text = clean_text(text)
    measurement_canvas = Image.new("RGBA", (max_width, 800), (0, 0, 0, 0))
    draw = ImageDraw.Draw(measurement_canvas)
    inner_width = max(1, max_width - (padding * 2))

    chosen_font = None
    wrapped_text = text
    bbox = (0, 0, inner_width, font_size)
    fitted = False
    for size in range(font_size, min_font_size - 1, -4):
        chosen_font = _load_caption_font(size)
        candidate = _wrap_text(
            draw,
            text,
            chosen_font,
            inner_width,
            stroke_width,
        )
        candidate_bbox = draw.multiline_textbbox(
            (0, 0),
            candidate,
            font=chosen_font,
            spacing=8,
            align="center",
            stroke_width=stroke_width,
        )
        if (
            (not max_lines or len(candidate.splitlines()) <= max_lines)
            and candidate_bbox[2] - candidate_bbox[0] <= inner_width
        ):
            wrapped_text = candidate
            bbox = candidate_bbox
            fitted = True
            break

    # Only truncate after trying the full phrase at every allowed font size.
    # This keeps ordinary 3--5 word cover lines intact while still enforcing
    # the two-line ceiling for unusually long words.
    if not fitted:
        chosen_font = _load_caption_font(min_font_size)
        words = text.split()
        for kept_word_count in range(len(words) - 1, 0, -1):
            shortened = " ".join(words[:kept_word_count]).rstrip(".,;:!?")
            shortened += "…"
            candidate = _wrap_text(
                draw,
                shortened,
                chosen_font,
                inner_width,
                stroke_width,
            )
            candidate_bbox = draw.multiline_textbbox(
                (0, 0),
                candidate,
                font=chosen_font,
                spacing=8,
                align="center",
                stroke_width=stroke_width,
            )
            if (
                (not max_lines or len(candidate.splitlines()) <= max_lines)
                and candidate_bbox[2] - candidate_bbox[0] <= inner_width
            ):
                wrapped_text = candidate
                bbox = candidate_bbox
                fitted = True
                break

        # A single very wide word cannot be wrapped. Shorten it by characters
        # instead of accepting an empty string, so the cover can never vanish.
        if not fitted:
            first_word = words[0] if words else text
            for kept_character_count in range(len(first_word) - 1, 0, -1):
                candidate = (
                    first_word[:kept_character_count].rstrip(".,;:!?") + "…"
                )
                candidate_bbox = draw.multiline_textbbox(
                    (0, 0),
                    candidate,
                    font=chosen_font,
                    spacing=8,
                    align="center",
                    stroke_width=stroke_width,
                )
                if candidate_bbox[2] - candidate_bbox[0] <= inner_width:
                    wrapped_text = candidate
                    bbox = candidate_bbox
                    fitted = True
                    break

        if not fitted:
            wrapped_text = "…"
            bbox = draw.multiline_textbbox(
                (0, 0),
                wrapped_text,
                font=chosen_font,
                spacing=8,
                align="center",
                stroke_width=stroke_width,
            )

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


def _caption_word_lines(
    draw: ImageDraw.ImageDraw,
    words: list,
    font,
    max_width: int,
    stroke_width: int,
) -> list:
    """Wrap caption word indexes while keeping their timing identity."""
    lines = []
    current = []
    for index, word in enumerate(words):
        candidate = [*current, index]
        text = " ".join(words[i] for i in candidate)
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = [index]
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_caption_overlay(
    words: list,
    max_width: int,
    active_index: int = None,
    active_only: bool = False,
    font_size: int = CAPTION_FONT_SIZE,
    min_font_size: int = CAPTION_MIN_FONT_SIZE,
    stroke_width: int = CAPTION_STROKE_WIDTH,
    padding: int = 18,
) -> Image.Image:
    """Render one caption cue, optionally tinting only its active word."""
    words = [clean_text(str(word)) for word in words if clean_text(str(word))]
    if not words:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

    measurement = Image.new("RGBA", (max_width, 800), (0, 0, 0, 0))
    measure_draw = ImageDraw.Draw(measurement)
    inner_width = max(1, max_width - (padding * 2))
    chosen_font = None
    chosen_size = min_font_size
    lines = []
    for size in range(font_size, min_font_size - 1, -4):
        chosen_size = size
        chosen_font = _load_caption_font(size)
        lines = _caption_word_lines(
            measure_draw, words, chosen_font, inner_width, stroke_width
        )
        widest = max(
            measure_draw.textbbox(
                (0, 0),
                " ".join(words[i] for i in line),
                font=chosen_font,
                stroke_width=stroke_width,
            )[2]
            for line in lines
        )
        if widest <= inner_width:
            break

    line_height = int(math.ceil(chosen_size * 1.22)) + (stroke_width * 2)
    line_widths = [
        float(
            measure_draw.textlength(
                " ".join(words[i] for i in line), font=chosen_font
            )
        )
        for line in lines
    ]
    image_width = int(min(max_width, max(line_widths) + (padding * 2) + (stroke_width * 2)))
    image_height = max(1, (padding * 2) + (line_height * len(lines)))
    image = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    for line_number, line in enumerate(lines):
        line_text = " ".join(words[i] for i in line)
        y = padding + (line_number * line_height)
        if not active_only:
            draw.text(
                (image_width / 2, y),
                line_text,
                font=chosen_font,
                fill=(255, 255, 255, 255),
                anchor="ma",
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 255),
            )

        if active_index is not None and active_index in line:
            active_position = line.index(active_index)
            prefix = " ".join(words[i] for i in line[:active_position])
            prefix_with_space = f"{prefix} " if prefix else ""
            active_x = (
                (image_width - line_widths[line_number]) / 2
                + float(measure_draw.textlength(prefix_with_space, font=chosen_font))
            )
            draw.text(
                (active_x, y),
                words[active_index],
                font=chosen_font,
                fill=CAPTION_ACTIVE_COLOR,
                anchor="la",
                stroke_width=stroke_width,
                stroke_fill=(0, 0, 0, 255),
            )
    return image


def _caption_pop_scale(t: float) -> float:
    """Scale a caption from 90% to 100% over its first 100ms."""
    progress = min(1.0, max(0.0, t) / CAPTION_POP_DURATION)
    return 0.9 + (0.1 * progress)


def create_caption_clips(caption_groups: list) -> list:
    """Create lower-third cues plus per-word karaoke highlight overlays."""
    clips = []
    max_width = VIDEO_WIDTH - (CAPTION_SIDE_MARGIN * 2)
    for group in caption_groups:
        group_start = float(group["start"])
        group_end = float(group["end"])
        duration = max(0.05, group_end - group_start)
        timed_words = group.get("words") or []
        if not timed_words:
            # Backward compatibility for callers/tests that construct legacy
            # groups without word metadata.
            texts = str(group.get("text", "")).split()
            slice_duration = duration / max(1, len(texts))
            timed_words = [
                {
                    "text": text,
                    "start": group_start + (index * slice_duration),
                    "end": group_start + ((index + 1) * slice_duration),
                }
                for index, text in enumerate(texts)
            ]
        word_texts = [str(word["text"]) for word in timed_words]
        image = render_caption_overlay(
            word_texts,
            max_width=max_width,
        )
        top = max(0, CAPTION_SAFE_BOTTOM - image.height)
        clip = (
            ImageClip(np.array(image), transparent=True)
            .set_start(group_start)
            .set_duration(duration)
            .set_position(("center", top))
            .fx(vfx.resize, _caption_pop_scale)
        )
        clips.append(clip)

        for word_index, word in enumerate(timed_words):
            active_start = max(group_start, float(word["start"]))
            active_end = min(group_end, float(word["end"]))
            if active_end <= active_start:
                continue
            active_image = render_caption_overlay(
                word_texts,
                max_width=max_width,
                active_index=word_index,
                active_only=True,
            )
            pop_offset = max(0.0, active_start - group_start)
            active_clip = (
                ImageClip(np.array(active_image), transparent=True)
                .set_start(active_start)
                .set_duration(max(0.05, active_end - active_start))
                .set_position(("center", top))
                .fx(
                    vfx.resize,
                    lambda t, offset=pop_offset: _caption_pop_scale(t + offset),
                )
            )
            clips.append(active_clip)
    return clips


def _env_flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _speech_intervals(timed_words: list, duration: float) -> list:
    """Merge Whisper word timings into speech intervals for music ducking."""
    raw = []
    for word in timed_words or []:
        start = max(0.0, float(word.get("start", 0.0)) - 0.035)
        end = min(duration, max(start + 0.05, float(word.get("end", start))) + 0.035)
        raw.append((start, end))
    if not raw:
        # The safe fallback is to assume continuous speech. A failed Whisper
        # model must never leave gap-level music competing with the narration.
        return [(0.0, max(0.05, duration))]

    merged = []
    for start, end in sorted(raw):
        if merged and start <= merged[-1][1] + 0.12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _music_gain_for_time(t, speech_intervals: list):
    """Return a scalar/vector sidechain envelope for MoviePy audio frames."""
    times = np.asarray(t, dtype=float)
    gap_gain = 10 ** (MUSIC_GAP_DB / 20.0)
    ducked_gain = 10 ** (MUSIC_DUCKED_DB / 20.0)
    gains = np.full(times.shape, gap_gain, dtype=float)

    for start, end in speech_intervals:
        inside = (times >= start) & (times <= end)
        gains = np.where(inside, np.minimum(gains, ducked_gain), gains)

        attack_start = max(0.0, start - MUSIC_DUCK_ATTACK_SECONDS)
        attack = (times >= attack_start) & (times < start)
        if start > attack_start:
            attack_progress = (times - attack_start) / (start - attack_start)
            attack_gain = gap_gain + ((ducked_gain - gap_gain) * attack_progress)
            gains = np.where(attack, np.minimum(gains, attack_gain), gains)

        release_end = end + MUSIC_DUCK_RELEASE_SECONDS
        release = (times > end) & (times <= release_end)
        if release_end > end:
            release_progress = (times - end) / (release_end - end)
            release_gain = ducked_gain + ((gap_gain - ducked_gain) * release_progress)
            gains = np.where(release, np.minimum(gains, release_gain), gains)

    if np.ndim(t) == 0:
        return float(gains)
    return gains


def create_music_mix(
    narration_audio,
    timed_words: list,
    duration: float,
    article_id: int,
    music_dir: Path = MUSIC_DIR,
):
    """Mix a deterministic bundled music loop under narration.

    Returns ``(audio_clip, resources)``. On any missing/invalid music asset the
    original narration is returned unchanged so music can never fail a render.
    """
    tracks = sorted(
        path
        for path in Path(music_dir).glob("*")
        if path.suffix.lower() in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
    )
    if not tracks:
        logger.warning("[Music] No bundled music tracks found; continuing voice-only")
        return narration_audio, []

    resources = []
    try:
        track_path = tracks[int(article_id) % len(tracks)]
        source = AudioFileClip(str(track_path))
        resources.append(source)
        if not source.duration or source.duration <= 0.05:
            raise ValueError("music track is empty")

        peak = float(source.max_volume())
        if not math.isfinite(peak) or peak <= 1e-6:
            raise ValueError("music track is silent")
        normalized = source.volumex(1.0 / peak)
        resources.append(normalized)
        looped = normalized.fx(afx.audio_loop, duration=duration)
        resources.append(looped)
        intervals = _speech_intervals(timed_words, duration)

        def apply_envelope(get_frame, frame_time):
            frame = np.asarray(get_frame(frame_time))
            gains = _music_gain_for_time(frame_time, intervals)
            if np.ndim(gains) and frame.ndim > 1:
                gains = np.asarray(gains)[:, None]
            return frame * gains

        music = looped.fl(apply_envelope, keep_duration=True)
        music = music.fx(
            afx.audio_fadein, min(MUSIC_FADE_IN_SECONDS, duration)
        ).fx(
            afx.audio_fadeout, min(MUSIC_FADE_OUT_SECONDS, duration)
        )
        resources.append(music)
        voice = narration_audio.volumex(NARRATION_MIX_GAIN)
        resources.append(voice)
        mixed = CompositeAudioClip([voice, music]).set_duration(duration)
        resources.append(mixed)
        logger.info(
            "[Music] Mixed '%s' at %.0f dB speech / %.0f dB gaps",
            track_path.name,
            MUSIC_DUCKED_DB,
            MUSIC_GAP_DB,
        )
        return mixed, resources
    except Exception as exc:
        logger.warning("[Music] Mix failed; continuing voice-only: %s", exc)
        for resource in reversed(resources):
            try:
                resource.close()
            except Exception:
                pass
        return narration_audio, []


def create_headline_clip(
    title: str,
    duration: float,
    cover_line: str | None = None,
):
    """Create the short, high-impact cover line shown during the opening hook."""
    headline = clean_text(cover_line or "")
    if not headline:
        headline = " ".join(clean_text(title).split()[:5])
    headline = " ".join(headline.split()[:5]).upper()
    if not headline or duration <= 0:
        return None

    image = render_text_overlay(
        headline,
        max_width=VIDEO_WIDTH - 80,
        font_size=144,
        min_font_size=96,
        stroke_width=9,
        padding=26,
        max_lines=2,
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


def generate_themed_images(
    title: str,
    script: str,
    num_images: int = NUM_BODY_IMAGES,
    image_source: str = "ai",
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
) -> list:
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
        repeats = max(1, math.ceil(num_images / max(1, len(keywords))))
        prompts = [
            f"{kw}, {setting}, {style}"
            for kw in (keywords * repeats)[:num_images]
        ]

    color_guidance = color_intensity_prompt_guidance(color_intensity)
    prompts = [
        (
            f"{prompt}, {SHOT_TYPES[index % len(SHOT_TYPES)]}, "
            f"{color_guidance}"
        )
        for index, prompt in enumerate(prompts)
    ]
    logger.info(
        "[Cost] Legacy body stills: premium=0 body=%d estimated=$%.3f",
        len(prompts),
        estimate_ai_still_cost(0, len(prompts)),
    )
    return _parallel_image_gen(prompts)


def _parallel_image_gen(
    prompts: list,
    *,
    premium_flags: list[bool] | None = None,
) -> list:
    """Generate N images in parallel. Returns list[PIL.Image] in prompt order."""
    premium_flags = premium_flags or [False] * len(prompts)
    images = [None] * len(prompts)

    def generate(index: int, prompt: str):
        if index < len(premium_flags) and premium_flags[index]:
            return generate_image_fal(
                prompt,
                model=FAL_HOOK_IMAGE_MODEL,
                num_inference_steps=None,
            )
        return generate_image_fal(
            prompt,
            model=FAL_IMAGE_MODEL,
            num_inference_steps=FAL_IMAGE_STEPS,
        )

    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate, i, p): i
            for i, p in enumerate(prompts)
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


def generate_scene_images(
    shots: list,
    style_key: str,
    image_source: str = "ai",
    *,
    series_lane: str | None = None,
    hero_image: str | None = None,
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
    visual_sources_out: list | None = None,
) -> list:
    """Generate one image per timed shot, preserving narrative order.

    Legacy ``mixed`` mode may use a real article hero. Topic/category metadata
    never decides sourcing; scene-based renders use referent facts above.
    """
    # New scene records describe observable referent facts.  Once present,
    # those facts—not topic, style, or series_lane—own visual sourcing.
    if shots and any("referent" in (shot or {}) for shot in shots):
        return generate_referent_scene_images(
            shots,
            color_intensity=color_intensity,
            visual_sources_out=visual_sources_out,
            hero_image=hero_image,
        )

    if image_source == "stock":
        logger.info(f"[Video] Fetching {len(shots)} shot-aligned stock photos...")
        images = []
        query_offsets = {}
        for index, shot in enumerate(shots):
            base_query = " ".join(
                (shot.get("visual") or shot.get("speech") or "").split()[:6]
            )
            base_query = base_query or "science"
            shot_type = (
                shot.get("_shot_type")
                or SHOT_TYPES[index % len(SHOT_TYPES)]
            )
            offset = query_offsets.get(base_query.casefold(), 0)
            query_offsets[base_query.casefold()] = offset + 1
            found = search_pexels_images(
                f"{base_query} {shot_type}",
                min(len(SHOT_TYPES), offset + 1),
            )
            images.append(
                found[offset % len(found)]
                if found
                else create_gradient_background()
            )
        return images

    from visual_styles import apply_style

    prompts = []
    premium_flags = []
    color_guidance = color_intensity_prompt_guidance(color_intensity)
    for index, shot in enumerate(shots):
        shot_type = shot.get("_shot_type") or SHOT_TYPES[index % len(SHOT_TYPES)]
        visual = shot.get("visual") or shot.get("speech") or "science discovery"
        prompts.append(
            apply_style(
                f"{visual}, {shot_type}, {color_guidance}",
                style_key,
                is_hook=False,
            )
        )
        premium_flags.append(int(shot.get("_scene_index", index)) < 2)

    logger.info(
        "[Video] Generating %d shot images in style '%s' (source=%s)...",
        len(prompts),
        style_key,
        image_source,
    )
    if image_source != "mixed":
        if style_key == "illustrated_science":
            unique_positions = []
            scene_to_unique = {}
            slot_to_unique = []
            for index, shot in enumerate(shots):
                try:
                    scene_index = int(shot.get("_scene_index", index))
                except (TypeError, ValueError):
                    scene_index = index
                if scene_index not in scene_to_unique:
                    scene_to_unique[scene_index] = len(unique_positions)
                    unique_positions.append(index)
                slot_to_unique.append(scene_to_unique[scene_index])
            unique_prompts = [prompts[index] for index in unique_positions]
            unique_premium = [premium_flags[index] for index in unique_positions]
            premium_count = sum(unique_premium)
            body_count = len(unique_premium) - premium_count
            logger.info(
                "[Cost] Illustrated scenes: premium=%d body=%d reused_slots=%d estimated=$%.3f",
                premium_count,
                body_count,
                len(shots) - len(unique_positions),
                estimate_ai_still_cost(premium_count, body_count),
            )
            unique_images = _parallel_image_gen(
                unique_prompts,
                premium_flags=unique_premium,
            )
            return [unique_images[unique_index] for unique_index in slot_to_unique]
        premium_count = sum(premium_flags)
        body_count = len(premium_flags) - premium_count
        logger.info(
            "[Cost] Body stills: premium=%d body=%d real=0 estimated=$%.3f",
            premium_count,
            body_count,
            estimate_ai_still_cost(premium_count, body_count),
        )
        return _parallel_image_gen(prompts, premium_flags=premium_flags)

    from real_imagery import fetch_hero_image

    desired_sources = ["ai"] * len(shots)
    if hero_image and shots:
        desired_sources[0] = "hero"

    images = [None] * len(shots)
    actual_sources = ["ai"] * len(shots)
    def generate_slot(index: int):
        source = desired_sources[index]
        image = None
        if source == "hero":
            image = fetch_hero_image(
                hero_image,
                resize_fn=resize_and_crop_image,
                target_width=VIDEO_WIDTH,
                target_height=VIDEO_HEIGHT,
            )
        if image is not None:
            return image, source

        if premium_flags[index]:
            image = generate_image_fal(
                prompts[index],
                model=FAL_HOOK_IMAGE_MODEL,
                num_inference_steps=None,
            )
        else:
            image = generate_image_fal(
                prompts[index],
                model=FAL_IMAGE_MODEL,
                num_inference_steps=FAL_IMAGE_STEPS,
            )
        return image, "ai"

    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate_slot, index): index
            for index in range(len(shots))
        }
        for future in as_completed(future_to_idx):
            index = future_to_idx[future]
            try:
                images[index], actual_sources[index] = future.result()
            except Exception as exc:
                logger.info(
                    "[Video] Mixed image %d failed after fallback: %s",
                    index + 1,
                    exc,
                )
                images[index] = create_gradient_background()

    logger.info(
        "[Video] Mixed imagery used hero=%d NASA=%d AI=%d",
        actual_sources.count("hero"),
        actual_sources.count("nasa"),
        actual_sources.count("ai"),
    )
    premium_ai = sum(
        source == "ai" and premium_flags[index]
        for index, source in enumerate(actual_sources)
    )
    body_ai = sum(
        source == "ai" and not premium_flags[index]
        for index, source in enumerate(actual_sources)
    )
    real_count = len(actual_sources) - premium_ai - body_ai
    logger.info(
        "[Cost] Body stills: premium=%d body=%d real=%d estimated=$%.3f",
        premium_ai,
        body_ai,
        real_count,
        estimate_ai_still_cost(premium_ai, body_ai),
    )
    return images


def _credit_photo(image: Image.Image, source) -> Image.Image:
    """Burn a compact credit for any licence that requires attribution."""
    from real_imagery import _is_public_domain

    if _is_public_domain(source.license):
        return image
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result, "RGBA")
    credit = "Image: " + " · ".join(
        part for part in (source.author, source.license, source.source_name) if part
    )
    font = _load_caption_font(24)
    max_width = VIDEO_WIDTH - 140
    while draw.textbbox((0, 0), credit, font=font)[2] > max_width and len(credit) > 24:
        credit = credit[:-2].rstrip() + "…"
    box = draw.textbbox((0, 0), credit, font=font)
    width = box[2] - box[0]
    # Sit below the caption safe area rather than at the top, where the credit
    # was landing directly on the hook line and stealing the opening frame.
    top = VIDEO_HEIGHT - 96
    draw.rounded_rectangle(
        (VIDEO_WIDTH - width - 82, top, VIDEO_WIDTH - 52, top + 52),
        radius=18,
        fill=(8, 18, 27, 190),
    )
    draw.text(
        (VIDEO_WIDTH - width - 67, top + 10),
        credit,
        font=font,
        fill=(255, 255, 255, 225),
    )
    return result


def _schematic_prompt(
    scene: dict,
    color_intensity: str,
    article_title: str = "",
) -> str:
    visual = str(scene.get("visual") or scene.get("speech") or "science concept")
    evidence_query = str(scene.get("evidence_query") or "").strip()
    return (
        f"Premium flat editorial science illustration for the story '{article_title}'. "
        f"The exact discovery or evidence to keep central is '{evidence_query}'. "
        f"Clearly communicate {visual}. Show the discovered thing or measured effect, "
        "not merely the city, institution, building, or broad setting where it happened. "
        "Warm off-white field, cobalt blue "
        "shapes, restrained yellow highlight and red only for emphasis, simple geometric "
        "silhouettes, intentionally illustrated, vertical 9:16. Show a recognizable focal "
        "subject, not an empty composition or a lone abstract shape. No unrelated animals, "
        "gods, or objects. No photorealism, no human "
        "hands, no faces, no crowds, no signatures, no watermarks, no words, no letters, "
        "no labels, no cutaway, no cross-section, no microscopy, no measurement scale. "
        f"{color_intensity_prompt_guidance(color_intensity)}"
    )


def _symbolic_prompt(
    scene: dict,
    color_intensity: str,
    article_title: str = "",
) -> str:
    """Prompt for scenes that must not depict structure.

    Used for abstract subjects and for precise claims about things nobody has
    photographed. ``_schematic_prompt`` says "schematic of X", which invites the
    model to invent a diagram -- that is how an earlier render produced a whisker
    follicle that looked like a plant root. This asks for mood and metaphor
    instead, so the frame carries the narration without asserting a fact.
    """
    narration = str(scene.get("speech") or "discovery")
    visual_reference = str(scene.get("visual") or narration)
    evidence_query = str(scene.get("evidence_query") or "").strip()
    return (
        f"Premium editorial science illustration for the story '{article_title}'. "
        f"The narration is: {narration}. Use this only as safe visual inspiration: "
        f"{visual_reference}. The exact discovery or evidence to keep central is "
        f"'{evidence_query}'. Show that finding, result, specimen, or mechanism rather "
        "than merely the city, institution, building, or broad setting. Show a clear, "
        "recognizable focal subject explicitly named "
        "in the story or scene, using lighting, pose, scale, or environment to communicate "
        "the idea. Never substitute an unrelated animal, deity, organ, machine, or object. "
        "Do not output an empty composition, decorative-only geometry, or one lone abstract "
        "shape. Warm off-white field, cobalt blue and restrained yellow, clean editorial "
        "collage, intentionally illustrated, vertical 9:16. Symbolic and atmospheric only; "
        "do not assert hidden structure. No technical diagram, no anatomy, no cutaway, no "
        "cross-section, no microscopy, no measurement scale, no photorealism, no human "
        "hands, no human faces, no crowds, no signatures, no watermarks, no words, no "
        "letters, no labels. "
        f"{color_intensity_prompt_guidance(color_intensity)}"
    )


def _documentary_photo_variant(
    image: Image.Image,
    shot: dict,
    variant_index: int,
) -> Image.Image:
    """Create a distinct editorial crop without inventing visual evidence."""
    source = resize_and_crop_image(image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    shot_type = str((shot or {}).get("_shot_type") or "").casefold()
    if "macro" in shot_type:
        scale = 1.18
    elif "close" in shot_type:
        scale = 1.12
    elif "wide" in shot_type:
        scale = 1.035
    else:
        scale = 1.075 + (0.015 * (variant_index % 3))

    width = max(VIDEO_WIDTH, int(round(VIDEO_WIDTH * scale)))
    height = max(VIDEO_HEIGHT, int(round(VIDEO_HEIGHT * scale)))
    enlarged = source.resize((width, height), Image.Resampling.LANCZOS)
    max_x = max(0, width - VIDEO_WIDTH)
    max_y = max(0, height - VIDEO_HEIGHT)
    anchors = (
        (0.50, 0.42),
        (0.18, 0.30),
        (0.82, 0.34),
        (0.30, 0.72),
        (0.70, 0.68),
    )
    anchor_x, anchor_y = anchors[variant_index % len(anchors)]
    left = int(round(max_x * anchor_x))
    top = int(round(max_y * anchor_y))
    edited = enlarged.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))
    edited = ImageEnhance.Contrast(edited).enhance(1.06)
    edited = ImageEnhance.Color(edited).enhance(1.04)

    # A restrained cinematic grade keeps captions readable while preserving
    # the photograph as the visual—not a card, diagram, or generated scene.
    rgba = edited.convert("RGBA")
    overlay = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, 0, VIDEO_WIDTH, 250), fill=(7, 12, 25, 38))
    draw.rectangle((0, 1450, VIDEO_WIDTH, VIDEO_HEIGHT), fill=(7, 12, 25, 54))
    return Image.alpha_composite(rgba, overlay).convert("RGB")


def _documentary_frame_image(
    image: Image.Image,
    target_width: int,
    target_height: int,
) -> Image.Image:
    """Keep the photographed evidence visible in a polished vertical frame.

    Archive and museum photographs are commonly landscape, square, or place
    the subject away from the centre. A destructive 9:16 crop can therefore
    turn a valid source into an apparently empty frame. Close-to-vertical
    sources remain full bleed; other aspect ratios are shown intact over a
    restrained, blurred extension of the same photograph.
    """
    source = image.convert("RGB")
    source_ratio = source.width / max(1, source.height)
    target_ratio = target_width / max(1, target_height)
    if abs(source_ratio - target_ratio) <= 0.08:
        return resize_and_crop_image(source, target_width, target_height)

    # A moderately wide source is better cropped than pillarboxed: the blurred
    # extension reads as letterboxing on a phone even though it is not black.
    # Only genuinely wide or tall sources get the blurred surround.
    if 0.62 <= source_ratio / max(0.01, target_ratio) <= 1.9:
        return resize_and_crop_image(source, target_width, target_height)

    background = resize_and_crop_image(source, target_width, target_height)
    background = background.filter(
        ImageFilter.GaussianBlur(radius=max(18, target_width // 28))
    )
    background = ImageEnhance.Brightness(background).enhance(0.66)
    background = ImageEnhance.Color(background).enhance(0.82)

    foreground = source.copy()
    foreground.thumbnail(
        (target_width, int(round(target_height * 0.9))),
        Image.Resampling.LANCZOS,
    )
    x = (target_width - foreground.width) // 2
    y = int(round((target_height - foreground.height) * 0.43))

    framed = background.convert("RGBA")
    shadow = Image.new("RGBA", framed.size, (0, 0, 0, 0))
    shadow_box = Image.new(
        "RGBA",
        (foreground.width + 34, foreground.height + 34),
        (0, 0, 0, 150),
    ).filter(ImageFilter.GaussianBlur(18))
    shadow.alpha_composite(shadow_box, (x - 17, y - 8))
    framed = Image.alpha_composite(framed, shadow)
    framed.alpha_composite(foreground.convert("RGBA"), (x, y))
    return framed.convert("RGB")


def _documentary_page_tones(image: Image.Image) -> tuple[float, float]:
    """Return the near-white share and mid-tone share of an image."""
    sample = image.convert("L")
    sample.thumbnail((640, 640), Image.Resampling.BILINEAR)
    histogram = sample.histogram()
    total = max(1, sum(histogram))
    return sum(histogram[220:]) / total, sum(histogram[80:220]) / total


def _documentary_is_diagram_scan(image: Image.Image) -> bool:
    """Detect a scanned schematic or labelled line diagram.

    Ink on a white page is strongly bimodal: a large near-white ground, dark
    strokes, and very little mid-tone. Photographs -- even bright ones like snow
    or a pale specimen background -- keep most of their pixels in the mid range.
    Measured tones rather than edge density, because edge density cannot tell a
    labelled schematic from a detailed photograph of foliage or a crowd.

    These frames are worth rejecting on their own terms: a viewer reads none of
    the 6pt labels in three seconds on a phone, and the printed text collides
    with the burned-in captions.
    """
    white_share, mid_share = _documentary_page_tones(image)
    return (
        white_share >= DOCUMENTARY_PAGE_WHITE_SHARE
        and mid_share <= DOCUMENTARY_PAGE_MID_SHARE
    )


def _documentary_image_is_usable(image: Image.Image) -> bool:
    """Reject empty frames and unreadable scanned diagrams."""
    sample = image.convert("L")
    sample.thumbnail((96, 96), Image.Resampling.BILINEAR)
    contrast = float(ImageStat.Stat(sample).stddev[0])
    edges = sample.filter(ImageFilter.FIND_EDGES)
    if edges.width > 4 and edges.height > 4:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    edge_energy = float(ImageStat.Stat(edges).mean[0])
    if not (contrast >= 20.0 or edge_energy >= 5.0):
        return False
    return not _documentary_is_diagram_scan(image)


_DOCUMENTARY_QUERY_STOPWORDS = frozenset({
    "ancient", "artwork", "close", "diagram", "egyptian", "for", "full", "image",
    "launch", "manuscript", "moon", "night", "photo", "photograph", "relief",
    "scene", "sky", "space", "temple", "the", "this", "view", "which", "wide",
})

_WEAK_EVIDENCE_TERMS = frozenset({
    "better", "changed", "changes", "different", "discovery", "effect", "finding",
    "found", "future", "important", "inside", "mystery", "new", "random", "research",
    "result", "results", "scientists", "study", "surprising", "unknown", "why",
})


def _scene_visual_role(scene: dict) -> str:
    role = str((scene or {}).get("visual_role") or "").strip().casefold()
    if role in {"discovery", "evidence", "mechanism", "context"}:
        return role
    # Older saved scenes predate visual_role. Recognize obvious location-only
    # queries so they remain confined to their one context beat.
    referent_tokens = _expanded_reference_tokens(
        str((scene or {}).get("referent_query") or "")
    )
    if referent_tokens & {
        "building", "campus", "city", "institute", "laboratory", "university",
    }:
        return "context"
    return "discovery"


def _useful_evidence_query(value: str) -> str:
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", str(value or ""))
        if len(word) >= 2
    ][:8]
    meaningful = [
        word for word in words
        if word.casefold() not in _DOCUMENTARY_QUERY_STOPWORDS
        and word.casefold() not in _WEAK_EVIDENCE_TERMS
        and word.casefold() not in {"their", "this", "that", "with", "from", "into", "over"}
    ]
    if not meaningful:
        return ""
    # A single exact scientific identifier (DNA, FGF21, K2-18b) is useful;
    # a lone generic word is not.
    if len(meaningful) == 1 and not (
        any(char.isdigit() for char in meaningful[0])
        or meaningful[0].isupper()
        or "-" in meaningful[0]
    ):
        return ""
    return " ".join(words).strip()


def _scene_evidence_query(scene: dict, article_title: str = "") -> str:
    """Return the discovery-first query used for search and generation.

    New summaries provide ``evidence_query`` directly. The fallbacks keep older
    saved articles renderable while preferring the actual result over a location.
    """
    scene = scene if isinstance(scene, dict) else {}
    for value in (
        scene.get("evidence_query"),
        scene.get("graphic_payload"),
        scene.get("focus_label"),
        scene.get("referent_query"),
    ):
        query = _useful_evidence_query(str(value or ""))
        if query:
            return query

    # Last resort for old scene JSON: combine a compact title subject with the
    # concrete on-screen description. This guides illustration generation but is
    # intentionally not allowed to degrade into a city-only query.
    title_words = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]*", str(article_title or ""))[:4]
    visual_words = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9-]*",
        str(scene.get("visual") or scene.get("speech") or ""),
    )
    combined = " ".join((*title_words, *visual_words[:4]))
    return _useful_evidence_query(combined)


def _documentary_proper_terms(*parts: str) -> list[str]:
    """Return genuinely proper nouns from the given text segments.

    A capitalised word that merely opens a sentence is not a proper noun.
    Treating it as one produced anchors like "Individual", "Millions" and
    "Cooperation", which then anchored two-word searches -- "Individual bundle"
    -- that kept none of the subject. An archive answered that literally and a
    story about cells bundling together got a photo of plastic bags.

    Each part is scanned separately so a title running into a sentence cannot
    hide the fact that a word is sitting in first position.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(part or "")):
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]+", sentence)
            for position, token in enumerate(tokens):
                clean = token.strip("-")
                if len(clean) < 3 or clean.casefold() in _DOCUMENTARY_QUERY_STOPWORDS:
                    continue
                if not (clean.isupper() or clean[:1].isupper()):
                    continue
                # Sentence-initial capitalisation carries no information unless
                # the token is also an acronym.
                if position == 0 and not clean.isupper():
                    continue
                if clean.casefold() in seen:
                    continue
                seen.add(clean.casefold())
                terms.append(clean)
    return terms


def _documentary_query_variants(
    scene: dict,
    article_title: str,
    query_override: str = "",
) -> list[str]:
    """Turn a multi-subject prompt into short, archive-friendly searches."""
    query = " ".join(str(
        query_override or (scene or {}).get("referent_query") or ""
    ).split()[:8]).strip()
    if not query:
        return []
    proper_terms = _documentary_proper_terms(
        str(article_title or ""),
        str((scene or {}).get("speech") or ""),
        query,
    )
    anchor = proper_terms[-1] if proper_terms else ""
    proper_keys = {term.casefold() for term in proper_terms}
    candidate_terms = [
        token
        for token in re.findall(r"[a-z0-9-]+", query.casefold())
        if len(token) >= 3
        and token not in _DOCUMENTARY_QUERY_STOPWORDS
        and token != anchor.casefold()
    ]
    # Pair the anchor with what the scene is *about*, not with another name from
    # the same list. "Syracuse University Siena Szeged sperm research" otherwise
    # yields "Szeged siena" and "Szeged university", which describe no subject at
    # all; the topic words have to outrank the institution words.
    subject_terms = [
        token for token in candidate_terms if token not in proper_keys
    ] + [
        token for token in candidate_terms if token in proper_keys
    ]
    # Search the exact discovery phrase first. Broader variants are fallbacks,
    # not substitutes; this prevents "Jerusalem" from outranking "DNA breaks".
    variants = [query]
    if anchor:
        for term in subject_terms[:3]:
            variants.append(f"{anchor} {term}")
    return list(dict.fromkeys(variants))[:4]


def _documentary_prefers_stock(query: str) -> bool:
    """Use photography search for atmospheric context, not museum metadata."""
    tokens = set(re.findall(r"[a-z0-9-]+", str(query or "").casefold()))
    artifact_terms = {
        "papyrus", "artifact", "relief", "temple", "sculpture", "statue",
        "manuscript", "painting", "fossil", "apparatus", "instrument",
    }
    return bool(
        tokens & {"desert", "night", "sky", "moon", "sunrise", "sunset"}
    ) and not bool(tokens & artifact_terms)


def _documentary_split_photo(left_image: Image.Image, right_image: Image.Image) -> Image.Image:
    """Create a clean two-source comparison frame from real photographs."""
    left = resize_and_crop_image(left_image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    right = resize_and_crop_image(right_image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    half = VIDEO_WIDTH // 2
    result = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (7, 12, 25))
    result.paste(left.crop((VIDEO_WIDTH // 4, 0, VIDEO_WIDTH // 4 + half, VIDEO_HEIGHT)), (0, 0))
    result.paste(right.crop((VIDEO_WIDTH // 4, 0, VIDEO_WIDTH // 4 + half, VIDEO_HEIGHT)), (half, 0))
    draw = ImageDraw.Draw(result)
    draw.rectangle((half - 4, 0, half + 4, VIDEO_HEIGHT), fill=(255, 205, 54))
    return result


_REFERENCE_ALIAS_GROUPS = (
    frozenset({"ibis", "bird", "birds", "beak", "feather", "feathers"}),
    frozenset({"baboon", "baboons", "monkey", "primate", "fur", "coat"}),
    frozenset({"whale", "whales", "orca", "orcas", "cetacean"}),
    frozenset({"shark", "sharks", "fish"}),
    frozenset({"moon", "lunar", "moonlight"}),
    frozenset({"spacecraft", "satellite", "probe", "mission"}),
    frozenset({"brain", "neuron", "neurons", "neural"}),
)


def _expanded_reference_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9-]+", str(value or "").casefold())
        if len(token) >= 3 and token not in _DOCUMENTARY_QUERY_STOPWORDS
    }
    expanded = set(tokens)
    for group in _REFERENCE_ALIAS_GROUPS:
        if tokens & group:
            expanded.update(group)
    return expanded


def _story_reference_assets(
    scenes: list[dict],
    image_pool: list[tuple[Image.Image, dict]],
    *,
    limit: int = 2,
) -> list[tuple[Image.Image, dict]]:
    """Pick recurring verified subjects for later concept-scene edits."""
    corpus_tokens = Counter()
    for scene in scenes:
        corpus_tokens.update(_expanded_reference_tokens(" ".join((
            str(scene.get("speech") or ""),
            str(scene.get("visual") or ""),
            str(scene.get("referent_query") or ""),
        ))))

    ranked = []
    seen_urls = set()
    for position, asset in enumerate(image_pool):
        _image, record = asset
        if record.get("provider") == "Pexels":
            continue
        source_url = str(record.get("source_url") or "")
        if source_url and source_url in seen_urls:
            continue
        query = str(record.get("search_query") or "")
        query_tokens = _expanded_reference_tokens(query)
        if not query_tokens:
            continue
        score = sum(corpus_tokens[token] for token in query_tokens)
        if score <= 0:
            continue
        ranked.append((score, -position, asset))
        seen_urls.add(source_url)
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [asset for _score, _position, asset in ranked[:max(0, limit)]]


def _reference_backed_scene_asset(
    references: list[tuple[Image.Image, dict]],
    edit_index: int,
) -> tuple[Image.Image, dict] | None:
    """Create varied edits while preserving the exact verified subject."""
    if not references:
        return None
    if len(references) >= 2 and edit_index % 3 == 0:
        left_image, left_record = references[0]
        right_image, right_record = references[1]
        record = dict(left_record)
        record["edit"] = "reference-backed split comparison"
        record["secondary_source_url"] = right_record.get("source_url", "")
        record["secondary_provider"] = right_record.get("provider", "")
        return _documentary_split_photo(left_image, right_image), record

    image, base_record = references[edit_index % len(references)]
    record = dict(base_record)
    record["edit"] = "reference-backed subject close-up"
    return image.copy(), record


def _contextual_reference_comparison(
    context_image: Image.Image,
    references: list[tuple[Image.Image, dict]],
) -> tuple[Image.Image, dict] | None:
    """Combine real context with the two verified subjects of a question."""
    if len(references) < 2:
        return None
    background = resize_and_crop_image(
        context_image.convert("RGB"),
        VIDEO_WIDTH,
        VIDEO_HEIGHT,
    )
    background = ImageEnhance.Brightness(background).enhance(0.68).convert("RGBA")
    comparison = _documentary_split_photo(references[0][0], references[1][0])
    comparison = resize_and_crop_image(comparison, 960, 1280).convert("RGBA")
    mask = Image.new("L", comparison.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, comparison.width - 1, comparison.height - 1),
        radius=28,
        fill=255,
    )
    shadow = Image.new("RGBA", background.size, (0, 0, 0, 0))
    shadow_panel = Image.new("RGBA", (992, 1312), (0, 0, 0, 155)).filter(
        ImageFilter.GaussianBlur(20)
    )
    shadow.alpha_composite(shadow_panel, (44, 352))
    result = Image.alpha_composite(background, shadow)
    result.paste(comparison, (60, 360), mask)

    left_record = dict(references[0][1])
    left_record["edit"] = "contextual reference-backed comparison"
    left_record["secondary_source_url"] = references[1][1].get("source_url", "")
    left_record["secondary_provider"] = references[1][1].get("provider", "")
    return result.convert("RGB"), left_record


def generate_referent_scene_images(
    shots: list,
    *,
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
    visual_sources_out: list | None = None,
    article_title: str = "",
    hero_image: str | None = None,
) -> list:
    """Build a documentary edit from relevant real images, never placeholders."""
    from real_imagery import fetch_hero_image, fetch_referent_image, _verify_subject
    from visual_router import PHOTO, SCHEMATIC, route_scene

    # Every subject check is anchored on the story, not on the per-scene search
    # string, so an image that matches the words but not the topic is rejected.
    story_subject = " ".join(str(article_title or "").split()) or "this science story"

    unique_positions = []
    scene_to_unique = {}
    slot_to_unique = []
    for index, shot in enumerate(shots):
        try:
            scene_index = int((shot or {}).get("_scene_index", index))
        except (TypeError, ValueError):
            scene_index = index
        if scene_index not in scene_to_unique:
            scene_to_unique[scene_index] = len(unique_positions)
            unique_positions.append(index)
        slot_to_unique.append(scene_to_unique[scene_index])

    scenes = [shots[index] for index in unique_positions]
    scene_assets: list[tuple[Image.Image, dict] | None] = [None] * len(scenes)
    image_pool: list[tuple[Image.Image, dict]] = []

    hero_asset: tuple[Image.Image, dict] | None = None
    if hero_image:
        hero = fetch_hero_image(
            hero_image,
            resize_fn=_documentary_frame_image,
            target_width=VIDEO_WIDTH,
            target_height=VIDEO_HEIGHT,
        )
        if hero is not None and _documentary_image_is_usable(hero):
            hero_record = {
                "lane": PHOTO,
                "provider": "Source article",
                "source_url": hero_image,
                "license": "Source article image",
                "author": "Source publisher",
                "subject_verified": True,
                "verification_method": "article hero",
            }
            hero_asset = (hero, hero_record)
            image_pool.append(hero_asset)

    # First collect exact attributable imagery for every scene, including
    # diagrams, simulations, specimens, and scientific figures. A scene's
    # discovery/evidence query outranks its place or institution. This means the
    # opening can replace a vague article hero with a better image of the finding.
    known_source_urls = {
        str(record.get("source_url") or "")
        for _image, record in image_pool
        if record.get("source_url")
    }
    def search_scene(index: int, scene: dict):
        found_assets = []
        referent_query = str(scene.get("referent_query") or "").strip()
        evidence_query = _scene_evidence_query(scene, article_title)
        if not evidence_query:
            return index, found_assets
        archive_queries = (
            []
            if _documentary_prefers_stock(evidence_query)
            else _documentary_query_variants(
                scene,
                article_title,
                evidence_query,
            )
        )
        for search_query in archive_queries:
            source = fetch_referent_image(
                search_query,
                resize_fn=_documentary_frame_image,
                target_width=VIDEO_WIDTH,
                target_height=VIDEO_HEIGHT,
                subject=story_subject,
            )
            if source is None or any(
                str(record.get("source_url") or "") == source.source_url
                for _image, record in found_assets
            ):
                continue
            if not _documentary_image_is_usable(source.image):
                logger.info(
                    "[DocumentaryVisuals] Rejected low-information source for %r: %s",
                    search_query,
                    source.source_url,
                )
                continue
            source_record = source.audit_record(lane=PHOTO)
            source_record["search_query"] = search_query
            source_record["evidence_query"] = evidence_query
            source_record["visual_role"] = _scene_visual_role(scene)
            asset = (_credit_photo(source.image, source), source_record)
            found_assets.append(asset)
            if len(found_assets) >= 2:
                break
        # Stock is useful for a photographable object or deliberate context.
        # It is not evidence for an invisible mechanism or a precise discovery,
        # and it must clear the same subject check as an archive image. Accepting
        # unverified stock is how generic photographs reached slots whose scene
        # they had nothing to do with.
        if not found_assets and (
            route_scene(scene) == PHOTO or _scene_visual_role(scene) == "context"
        ):
            stock_query = evidence_query or referent_query
            for stock in search_pexels_images(stock_query, 2):
                if not _documentary_image_is_usable(stock):
                    continue
                if _verify_subject(stock, stock_query, story_subject) is not True:
                    logger.info(
                        "[DocumentaryVisuals] Stock rejected for %r (subject %r)",
                        stock_query,
                        story_subject,
                    )
                    continue
                stock_url = (
                    "https://www.pexels.com/search/"
                    + requests.utils.quote(stock_query)
                    + "/"
                )
                stock_record = {
                    "lane": PHOTO,
                    "provider": "Pexels",
                    "source_url": stock_url,
                    "license": "Pexels license",
                    "author": "Pexels contributor",
                    "subject_verified": True,
                    "verification_method": "stock search + subject vision check",
                    "search_query": stock_query,
                    "evidence_query": evidence_query,
                    "visual_role": _scene_visual_role(scene),
                }
                stock_asset = (stock, stock_record)
                found_assets.append(stock_asset)
                break
        return index, found_assets

    # Archive searches are I/O-bound and each has its own hard deadline. Run a
    # small bounded group concurrently so asking for ten distinct visuals does
    # not make generation ten times slower.
    search_results: dict[int, list[tuple[Image.Image, dict]]] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(scenes)))) as executor:
        futures = [
            executor.submit(search_scene, index, scene)
            for index, scene in enumerate(scenes)
            if scene_assets[index] is None
        ]
        for future in as_completed(futures):
            try:
                index, found_assets = future.result()
                search_results[index] = found_assets
            except Exception as exc:
                logger.info("[DocumentaryVisuals] Scene search unavailable: %s", exc)

    for index, scene in enumerate(scenes):
        found_assets = []
        for asset in search_results.get(index, []):
            source_url = str(asset[1].get("source_url") or "")
            if source_url and source_url in known_source_urls:
                continue
            found_assets.append(asset)
            image_pool.append(asset)
            if source_url:
                known_source_urls.add(source_url)
        if not found_assets:
            continue
        primary_image, primary_record = found_assets[0]
        comparison_language = re.search(
            r"\b(?:both|different|either|two|versus|vs)\b",
            str(scene.get("speech") or ""),
            flags=re.IGNORECASE,
        )
        if len(found_assets) > 1 and comparison_language:
            secondary_image, secondary_record = found_assets[1]
            primary_image = _documentary_split_photo(primary_image, secondary_image)
            primary_record = dict(primary_record)
            primary_record["edit"] = "split-screen comparison"
            primary_record["secondary_source_url"] = secondary_record.get("source_url", "")
            primary_record["secondary_provider"] = secondary_record.get("provider", "")
        scene_assets[index] = (primary_image, primary_record)

    # The article hero is useful context, but it is not automatically the best
    # representation of the discovery. Use it only if the exact opening search
    # did not find anything better.
    if scenes and scene_assets[0] is None and hero_asset is not None:
        scene_assets[0] = hero_asset

    # Scenes with no exact photograph are generated as intentionally illustrated
    # frames rather than recycled or abandoned. _schematic_prompt already bans the
    # failure classes that made earlier renders read as fake: hands, faces, crowds,
    # cutaways, cross-sections, microscopy, and measurement scales. What it still
    # allows -- stylised, symbolic, atmospheric imagery -- is not the problem and is
    # what carries watch time. A photo-led video may safely contain these, because
    # the prompt forbids photorealism, so they read as deliberate illustration next
    # to a photograph rather than as a failed imitation of one.
    # The lane still decides *how* to generate. A scene that asserts structure it
    # cannot show gets a symbolic frame, never a diagram -- that guardrail is the
    # whole reason the plant-root follicle cannot recur.
    references = _story_reference_assets(scenes, image_pool)

    # A comparison question must show the established subjects, not a generic
    # stock result or a newly invented animal. Preserve a real stock scene when
    # it explains environment/context; replace only the explicit final choice.
    if len(references) >= 2:
        for index, asset in enumerate(scene_assets):
            if asset is None:
                continue
            _image, record = asset
            if record.get("provider") != "Pexels":
                continue
            if not re.search(
                r"\b(?:which|better|versus|vs)\b",
                str(scenes[index].get("speech") or ""),
                flags=re.IGNORECASE,
            ):
                continue
            replacement = _contextual_reference_comparison(
                asset[0],
                references,
            )
            if replacement is not None:
                scene_assets[index] = replacement

    missing = [index for index, asset in enumerate(scene_assets) if asset is None]
    generated_indexes = []
    for index in missing:
        comparison_scene = bool(re.search(
            r"\b(?:both|different|either|two|which|better|versus|vs)\b",
            str(scenes[index].get("speech") or ""),
            flags=re.IGNORECASE,
        ))
        scene_tokens = _expanded_reference_tokens(" ".join((
            _scene_evidence_query(scenes[index], article_title),
            str(scenes[index].get("speech") or ""),
            str(scenes[index].get("visual") or ""),
        )))
        relevant_references = [
            asset for asset in references
            if scene_tokens & _expanded_reference_tokens(" ".join((
                str(asset[1].get("search_query") or ""),
                str(asset[1].get("evidence_query") or ""),
            )))
        ]
        # Reuse is now a narrow editorial tool for an explicit comparison of
        # already-established matching subjects. It is never a generic fallback.
        reference_asset = (
            _reference_backed_scene_asset(relevant_references[:2], 0)
            if comparison_scene and len(relevant_references) >= 2
            else None
        )
        if reference_asset is not None:
            image, record = reference_asset
            record = dict(record)
            record["verification_method"] = (
                str(record.get("verification_method") or "verified reference")
                + "+reference-backed edit"
            )
            scene_assets[index] = (image, record)
        else:
            generated_indexes.append(index)

    if generated_indexes:
        generated = _parallel_image_gen([
            _schematic_prompt(scenes[index], color_intensity, article_title)
            if route_scene(scenes[index]) == SCHEMATIC
            else _symbolic_prompt(scenes[index], color_intensity, article_title)
            for index in generated_indexes
        ], premium_flags=[True] * len(generated_indexes))
        for index, image in zip(generated_indexes, generated):
            if image is None or not _documentary_image_is_usable(image):
                continue
            generated_lane = route_scene(scenes[index])
            scene_assets[index] = (image, {
                "lane": generated_lane,
                "provider": "FAL",
                "source_url": "",
                "license": "Generated illustration",
                "author": "AI generated",
                "subject_verified": False,
                "verification_method": "generated illustration (non-photorealistic)",
                "evidence_query": _scene_evidence_query(scenes[index], article_title),
                "visual_role": _scene_visual_role(scenes[index]),
            })

    images = []
    records = []
    for index, scene in enumerate(scenes):
        asset = scene_assets[index]
        if asset is None:
            # Only reached if generation also failed for this slot.
            relevant_pool = [
                candidate for candidate in image_pool
                if _expanded_reference_tokens(_scene_evidence_query(scene, article_title))
                & _expanded_reference_tokens(" ".join((
                    str(candidate[1].get("search_query") or ""),
                    str(candidate[1].get("evidence_query") or ""),
                )))
            ]
            asset = (
                relevant_pool[0]
                if relevant_pool
                else (create_gradient_background(), {
                    "lane": SCHEMATIC,
                    "provider": "local",
                    "source_url": "",
                    "license": "Generated gradient",
                    "author": "Clipper",
                    "subject_verified": False,
                    "verification_method": "scene generation unavailable",
                    "evidence_query": _scene_evidence_query(scene, article_title),
                    "visual_role": _scene_visual_role(scene),
                })
            )
        image, base_record = asset
        record = dict(base_record)
        if scene_assets[index] is None and relevant_pool:
            record["editorial_reuse"] = True
            record["verification_method"] = (
                str(record.get("verification_method") or "documentary image")
                + "+editorial crop"
            )
        images.append(image)
        records.append(record)

    if visual_sources_out is not None:
        visual_sources_out.extend(records)
    logger.info(
        "[DocumentaryVisuals] scenes=%d photo=%d generated=%d reused=%d unique_sources=%d pool=%d",
        len(records),
        sum(record.get("lane") == PHOTO for record in records),
        sum(record.get("provider") == "FAL" for record in records),
        sum(bool(record.get("editorial_reuse")) for record in records),
        len({
            str(record.get("source_url") or f"generated:{index}")
            for index, record in enumerate(records)
        }),
        len(image_pool),
    )
    return [
        _documentary_photo_variant(
            images[unique_index],
            shot,
            int((shot or {}).get("_shot_step", slot_index)),
        )
        for slot_index, (shot, unique_index) in enumerate(zip(shots, slot_to_unique))
    ]


def create_hook_clips(
    title: str,
    duration: float = HOOK_DURATION,
    image_source: str = "ai",
    style_key: str = None,
    opening_visual: str = None,
    use_video_hook: bool | None = None,
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
    opening_image: Image.Image | None = None,
    opening_images: list[Image.Image] | None = None,
) -> list:
    """Create the hook sequence. Returns list[Clip].

    Hook mode resolution:
      - use_video_hook=True  -> AI video hook (env model OR DEFAULT_HOOK_VIDEO_MODEL)
      - use_video_hook=False -> always image hook
      - use_video_hook=None  -> env-driven (HOOK_VIDEO_MODEL set => video, else image)

    On any failure of the video path, falls back to the image hook so a flaky
    video model never breaks the pipeline. The hook video is generated with the
    SAME style preset (apply_style + style_key) as the body images, so the whole
    video reads as one consistent visual identity. Stock mode never uses the
    video hook (it exists for cheap test runs).
    """
    # Anchor on the opening scene visual if we have one; else derive from title.
    anchor = (opening_visual or title).strip().rstrip(",.")
    color_intensity = normalize_color_intensity(color_intensity)
    color_guidance = color_intensity_prompt_guidance(color_intensity)

    # Referent-routed videos reuse the already verified/code-rendered opening
    # frame. A second generated hook would reintroduce the visual fabrication
    # that this pipeline is designed to prevent.
    documentary_openers = list(opening_images or [])
    if not documentary_openers and opening_image is not None:
        documentary_openers = [opening_image]
    if documentary_openers:
        clip_duration = duration / NUM_HOOK_IMAGES
        return [
            create_clip(
                apply_color_intensity(
                    documentary_openers[index % len(documentary_openers)],
                    color_intensity,
                ),
                clip_duration,
                zoom_factor=0.038 + (0.006 * (index % 3)),
            )
            for index in range(NUM_HOOK_IMAGES)
        ]

    # Resolve which hook mode to run.
    if use_video_hook is True:
        video_model = HOOK_VIDEO_MODEL or DEFAULT_HOOK_VIDEO_MODEL
    elif use_video_hook is False:
        video_model = ""
    else:
        video_model = HOOK_VIDEO_MODEL  # env-driven default

    # --- AI video hook path (never in stock mode) ---
    if video_model and image_source != "stock":
        try:
            from visual_styles import apply_style
        except ImportError:
            apply_style = None

        motion_prompt = f"{anchor}, dramatic camera push-in, kinetic motion, dynamic energy"
        if style_key and apply_style:
            video_prompt = apply_style(motion_prompt, style_key, is_hook=True)
        else:
            video_prompt = f"{motion_prompt}, cinematic lighting, vertical 9:16, high energy, no text"
        video_prompt = f"{video_prompt}, {color_guidance}"

        local_mp4 = generate_hook_video_fal(video_prompt, video_model)
        if local_mp4:
            try:
                vclip = load_hook_video_clip(local_mp4, duration)
                motion_clips = split_motion_clip(vclip, duration)
                # Stash the shared temp path once so cleanup never unlinks it
                # before another slice has rendered.
                motion_clips[0]._scap_temp_path = local_mp4
                logger.info(f"[Hook] Using AI video hook ({duration:.1f}s)")
                return motion_clips
            except Exception as e:
                logger.info(f"[Hook] Video clip load failed: {e}, falling back to image hook")
                try:
                    Path(local_mp4).unlink(missing_ok=True)
                except OSError:
                    pass
        else:
            logger.info("[Hook] Video hook unavailable, falling back to image hook")

    # --- Image-based hook ---
    clip_duration = duration / NUM_HOOK_IMAGES

    if image_source == "stock":
        logger.info(f"[Hook] Fetching {NUM_HOOK_IMAGES} stock hook images...")
        images = search_pexels_images(title, NUM_HOOK_IMAGES)
    else:
        angle_variations = [
            f"{anchor}, extreme macro close-up, ultra sharp detail",
            f"{anchor}, impossible low-angle looking up, dramatic perspective",
            f"{anchor}, frozen peak action moment, motion blur trails",
            f"{anchor}, stark silhouette against explosive backdrop",
        ]

        if style_key == "illustrated_science":
            from visual_styles import apply_style
            hook_prompts = [
                apply_style(
                    f"{anchor}, one coherent teaching illustration, {color_guidance}",
                    style_key,
                    is_hook=True,
                )
            ]
        elif style_key:
            from visual_styles import apply_style
            hook_prompts = [
                apply_style(
                    f"{variation}, {color_guidance}",
                    style_key,
                    is_hook=True,
                )
                for variation in angle_variations
            ]
        else:
            # Legacy path (no style): use old generic punch prompts
            hook_prompts = [
                f"extreme macro close-up shot, {title}, ultra sharp detail, dramatic rim lighting, shallow depth of field, cinematic 9:16, hyper-realistic, {color_guidance}",
                f"impossible camera angle, {title}, bird's eye view mixed with dutch angle, dramatic shadows, high contrast neon accents, surreal perspective, {color_guidance}",
                f"frozen action moment, {title}, motion blur trails, dynamic energy, explosive composition, vibrant saturated colors, dramatic backlighting, {color_guidance}",
                f"bold graphic composition, {title}, stark contrast, complementary color explosion, minimalist but striking, professional advertising quality, {color_guidance}"
            ]

        image_count = 1 if style_key == "illustrated_science" else NUM_HOOK_IMAGES
        logger.info(f"[Hook] Creating {image_count} hook images in parallel (style: {style_key or 'legacy'})...")
        logger.info(
            "[Cost] Hook stills: premium=%d estimated=$%.3f",
            image_count,
            estimate_ai_still_cost(image_count, 0),
        )
        images = [None] * image_count
        with ThreadPoolExecutor(max_workers=image_count) as executor:
            future_to_idx = {
                executor.submit(
                    generate_image_fal,
                    prompt,
                    model=FAL_HOOK_IMAGE_MODEL,
                    num_inference_steps=None,
                ): i
                for i, prompt in enumerate(hook_prompts[:image_count])
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    images[idx] = future.result()
                except Exception:
                    images[idx] = create_gradient_background()

    images = apply_color_intensity_to_images(images, color_intensity)
    clips = [create_clip(img, clip_duration, zoom_factor=0.05) for img in images]
    logger.info(f"[Hook] Created {len(clips)} clips ({clip_duration:.2f}s each)")
    return clips


_HIGH_IMPACT_EMOTIONS = {
    "awe", "amazing", "curiosity", "dramatic", "exciting", "excitement",
    "fear", "hope", "shock", "surprise", "urgent", "wonder",
}


def select_motion_scene_indexes(scenes: list, limit: int) -> list:
    """Choose deterministic high-impact body scenes by emotion and position."""
    if limit <= 0 or len(scenes or []) <= 1:
        return []

    final_index = len(scenes) - 1
    position_peaks = {
        max(1, round(final_index * 0.33)),
        max(1, round(final_index * 0.66)),
        final_index,
    }
    ranked = []
    for index, scene in enumerate(scenes):
        if int(scene.get("_scene_index", index)) == 0:
            continue  # the hook already visualizes the opening scene
        emotion = _caption_token_key(scene.get("emotion", ""))
        score = 100 if any(term in emotion for term in _HIGH_IMPACT_EMOTIONS) else 0
        if index in position_peaks:
            score += 35
        if re.search(r"[!?]", str(scene.get("speech", ""))):
            score += 10
        # Stable tie-break: spread motion across the story before preferring
        # later scenes, instead of bunching every clip at the start.
        distance_to_peak = min(abs(index - peak) for peak in position_peaks)
        score -= distance_to_peak
        ranked.append((score, index))

    selected = [index for _score, index in sorted(ranked, key=lambda item: (-item[0], item[1]))[:limit]]
    return sorted(selected)


def create_body_motion_clips(
    scenes: list,
    durations: list,
    style_key: str,
    video_model: str,
    clip_limit: int,
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
) -> dict:
    """Generate selected body motion clips, returning ``{scene_index: clip}``.

    Every failure is represented by an absent dict entry; callers retain their
    already-generated still and Ken Burns fallback.
    """
    indexes = select_motion_scene_indexes(scenes, clip_limit)
    if not indexes:
        return {}
    color_guidance = color_intensity_prompt_guidance(color_intensity)

    logger.info(
        "[Cost] Planning %d body motion clip(s), estimated $%.2f",
        len(indexes),
        len(indexes) * VIDEO_CLIP_ESTIMATED_COST_USD,
    )

    def build_clip(index: int):
        scene = scenes[index]
        visual = (scene.get("visual") or scene.get("speech") or "science discovery").strip()
        motion_prompt = (
            f"{visual}, meaningful subject motion, cinematic camera movement, "
            "natural parallax, vertical 9:16, no text no words"
        )
        if style_key:
            try:
                from visual_styles import apply_style
                motion_prompt = apply_style(motion_prompt, style_key, is_hook=False)
            except Exception as exc:
                logger.info(f"[BodyVideo] Style application failed for scene {index}: {exc}")
        motion_prompt = f"{motion_prompt}, {color_guidance}"

        local_mp4 = generate_motion_video_fal(
            motion_prompt,
            video_model,
            log_label=f"BodyVideo:{index}",
        )
        if not local_mp4:
            return index, None
        try:
            target_duration = (
                durations[index]
                if index < len(durations)
                else DEFAULT_CHUNK_DURATION
            )
            target_duration = min(MAX_SHOT_DURATION, target_duration)
            clip = load_hook_video_clip(local_mp4, target_duration)
            clip._scap_temp_path = local_mp4
            return index, clip
        except Exception as exc:
            logger.info(
                f"[BodyVideo] Scene {index} clip load failed: {exc}; using still"
            )
            try:
                Path(local_mp4).unlink(missing_ok=True)
            except OSError:
                pass
            return index, None

    generated = {}
    with ThreadPoolExecutor(max_workers=min(3, len(indexes))) as executor:
        futures = {executor.submit(build_clip, index): index for index in indexes}
        for future in as_completed(futures):
            index = futures[future]
            try:
                result_index, clip = future.result()
                if clip is not None:
                    generated[result_index] = clip
                    logger.info(f"[BodyVideo] Using motion for scene {result_index}")
                else:
                    logger.info(f"[BodyVideo] Scene {result_index} fell back to still")
            except Exception as exc:
                logger.info(f"[BodyVideo] Scene {index} failed: {exc}; using still")
    return generated


def create_clip(
    image: Image.Image,
    duration: float,
    zoom_factor: float = 0.03,
    motion: str = "push",
) -> VideoClip:
    """Create a smooth varied Ken Burns move with no exposed frame edges."""
    source = resize_and_crop_image(image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    duration = max(0.05, float(duration))
    motion = motion if motion in SHOT_MOTIONS else "push"

    def make_frame(t: float) -> np.ndarray:
        progress = max(0.0, min(1.0, float(t) / duration))
        zoom = max(0.0, zoom_factor)
        if motion == "pull":
            scale = 1.0 + (zoom * (1.0 - progress))
        elif motion.startswith("pan-"):
            scale = 1.0 + max(0.035, zoom * 1.5)
        else:
            scale = 1.0 + (zoom * progress)
        zoom_width = max(VIDEO_WIDTH, int(math.ceil(VIDEO_WIDTH * scale)))
        zoom_height = max(VIDEO_HEIGHT, int(math.ceil(VIDEO_HEIGHT * scale)))
        zoomed = source.resize((zoom_width, zoom_height), Image.Resampling.LANCZOS)
        max_x = zoom_width - VIDEO_WIDTH
        max_y = zoom_height - VIDEO_HEIGHT
        if motion == "pan-left":
            left = int(round(max_x * (1.0 - progress)))
            top = max_y // 2
        elif motion == "pan-right":
            left = int(round(max_x * progress))
            top = max_y // 2
        else:
            left = max_x // 2
            top = max_y // 2
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


def compute_scene_durations(scenes: list, total_time: float) -> list:
    """Allocate time per scene proportional to its speech length."""
    if not scenes:
        return []
    weights = [max(1, len((s.get("speech") or "").split())) for s in scenes]
    total_w = sum(weights)
    if total_w <= 0:
        return [total_time / len(scenes)] * len(scenes)
    durations = [total_time * w / total_w for w in weights]
    durations[-1] += total_time - sum(durations)  # fix drift
    return [max(0.3, d) for d in durations]


def split_shot_duration(
    duration: float,
    max_duration: float = MAX_SHOT_DURATION,
) -> list[float]:
    """Split a visual hold without changing its total allocated time."""
    duration = max(0.05, float(duration))
    max_duration = max(0.05, float(max_duration))
    count = max(1, int(math.ceil(duration / max_duration)))
    piece = duration / count
    durations = [piece] * count
    durations[-1] += duration - sum(durations)
    return durations


def create_final_padding_clips(main_video, duration: float) -> list:
    """Represent even sub-frame final padding as explicit, shot-capped edits."""
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        return []

    count = max(1, int(math.ceil(duration / MAX_SHOT_DURATION)))
    piece = duration / count
    durations = [piece] * count
    durations[-1] += duration - sum(durations)
    last_frame_time = max(0.0, main_video.duration - (1.0 / FPS))
    return [
        main_video.to_ImageClip(t=last_frame_time).set_duration(piece)
        for piece in durations
    ]


def build_scene_shot_plan(scenes: list, total_time: float) -> list:
    """Expand narration scenes into deterministic, shot-capped visual beats."""
    plan = []
    scene_durations = compute_scene_durations(scenes, total_time)
    for scene_index, scene in enumerate(scenes):
        duration = (
            scene_durations[scene_index]
            if scene_index < len(scene_durations)
            else DEFAULT_CHUNK_DURATION
        )
        shot_durations = split_shot_duration(duration)
        for shot_step, shot_duration in enumerate(shot_durations):
            shot = dict(scene)
            shot["_scene_index"] = scene_index
            shot["_shot_step"] = shot_step
            shot["_scene_shot_count"] = len(shot_durations)
            shot["_shot_type"] = SHOT_TYPES[len(plan) % len(SHOT_TYPES)]
            shot["_duration"] = shot_duration
            plan.append(shot)
    return plan


def build_legacy_shot_plan(chunks: list, total_time: float) -> list:
    """Apply the same shot cap to pre-scene legacy scripts."""
    plan = []
    chunk_durations = compute_durations(chunks, total_time)
    for chunk_index, chunk in enumerate(chunks):
        duration = (
            chunk_durations[chunk_index]
            if chunk_index < len(chunk_durations)
            else DEFAULT_CHUNK_DURATION
        )
        for shot_duration in split_shot_duration(duration):
            plan.append(
                {
                    "speech": chunk,
                    "_chunk_index": chunk_index,
                    "_shot_type": SHOT_TYPES[len(plan) % len(SHOT_TYPES)],
                    "_duration": shot_duration,
                }
            )
    return plan


def allocate_render_paths(article_id: int, videos_dir: Path) -> tuple[Path, Path]:
    """Return collision-resistant output and private narration paths."""
    render_token = uuid4().hex
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = (
        videos_dir / f"article_{article_id}_{timestamp}_{render_token[:12]}.mp4"
    )
    narration_path = (
        Path(tempfile.gettempdir())
        / f"clipper_audio_{article_id}_{render_token}.wav"
    )
    return output_path, narration_path


def generate_video(
    article_id: int,
    title: str,
    script: str,
    image_source: str = "ai",
    captions: bool = True,
    scenes: list = None,
    style_key: str = None,
    emotion: str = None,
    use_video_hook: bool | None = None,
    voice_tone: str = tts_engine.DEFAULT_VOICE_TONE,
    cover_line: str | None = None,
    series_lane: str | None = None,
    hero_image: str | None = None,
    color_intensity: str = DEFAULT_COLOR_INTENSITY,
    visual_sources_out: list | None = None,
) -> str:
    """Generate TikTok-style video with parallel image generation.

    Preferred path: scenes + style_key + emotion provided (from summarizer).
    Each scene expands into style-consistent shots no longer than 2.5 seconds,
    which play in narrative order for the scene's proportional speech duration.
    `voice_tone` drives TTS voice, speed, and delivery styling. `emotion`
    remains scene metadata for visual storytelling.

    Fallback path: no scenes -> legacy themed-image generation with
    chunked text pacing.

    Args:
        image_source: 'ai' for FAL.ai, 'stock' for Pexels stock photos, or
            'mixed' for real hero/NASA imagery with per-slot AI fallback.
        captions: Burn word-synced captions into the video when True.
        use_video_hook: True forces the AI video hook, False forces stills,
            None follows the HOOK_VIDEO_MODEL env default.
        voice_tone: Narration preset id: controlled, energetic, or documentary.
        color_intensity: Still-image color preset: natural, vivid, or electric.
            Motion prompts receive matching palette guidance; motion files are
            not filtered frame by frame.
    """
    videos_dir = Path("static/videos")
    videos_dir.mkdir(parents=True, exist_ok=True)

    output_path, temp_audio_path = allocate_render_paths(article_id, videos_dir)

    audio = None
    main_video = None
    base_video = None
    clips = []
    overlay_clips = []
    music_resources = []
    actual_audio_path = None
    use_scenes = bool(scenes)
    color_intensity = normalize_color_intensity(color_intensity)
    from visual_styles import DEFAULT_STYLE
    style_key = style_key or DEFAULT_STYLE

    try:
        logger.info(
            f"Generating video for article {article_id} "
            f"(images: {image_source}, mode: {'scene-based' if use_scenes else 'legacy'}, "
            f"style: {style_key}, emotion: {emotion or 'default'}, "
            f"voice tone: {voice_tone or tts_engine.DEFAULT_VOICE_TONE}, "
            f"color: {color_intensity})"
        )

        # Step 1: TTS
        logger.info("Step 1: Generating voiceover...")
        narration_text = clean_text(script)
        actual_audio_path = tts_engine.synthesize(
            narration_text,
            str(temp_audio_path),
            emotion=emotion,
            voice_tone=voice_tone,
        )
        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        logger.info(f"Audio duration: {audio_duration:.1f}s")

        # Step 2: Word-level timings power both captions and music ducking, so
        # Whisper runs only once even when both features are enabled.
        music_enabled = _env_flag("MUSIC_ENABLED", True)
        timed_words = []
        caption_groups = []
        if captions or music_enabled:
            logger.info("Step 2: Transcribing word timings...")
            timed_words = transcribe_word_timestamps(
                actual_audio_path,
                script_text=narration_text,
            )
        if captions:
            caption_groups = group_words_for_captions(timed_words)
            logger.info("[Captions] Created %d caption groups", len(caption_groups))

        hook_len = min(HOOK_DURATION, max(2.0, audio_duration * 0.25))
        remaining = max(0.1, audio_duration - hook_len)
        chunks = []
        if use_scenes:
            body_shots = build_scene_shot_plan(scenes, remaining)
        else:
            chunks = chunk_text(script)
            body_shots = build_legacy_shot_plan(chunks, remaining)
        longest_body_shot = max(
            (float(shot["_duration"]) for shot in body_shots),
            default=0.0,
        )
        logger.info(
            "[Pacing] Planned %d body shots; longest %.3fs",
            len(body_shots),
            longest_body_shot,
        )
        planned_still_cost = estimate_planned_still_cost(
            body_shots,
            use_scenes=use_scenes,
            image_source=image_source,
            style_key=style_key,
        )
        render_motion_clip_cap = _effective_motion_clip_cap(
            _REQUESTED_MAX_VIDEO_CLIPS_PER_VIDEO,
            VIDEO_CLIP_ESTIMATED_COST_USD,
            base_cost=planned_still_cost,
        )
        logger.info(
            "[Cost] Planned conservative still estimate=$%.3f "
            "(source=%s, body shots=%d); per-render motion cap=%d",
            planned_still_cost,
            image_source,
            len(body_shots),
            render_motion_clip_cap,
        )

        # Step 3: Resolve visual style. Always resolve when the video hook is on
        # (legacy path included) so the AI video clip is generated with the SAME
        # style preset as the body images — otherwise the hook looks alien next
        # to the rest of the video.
        will_use_video_motion = (not use_scenes) and (
            use_video_hook is True
            or (use_video_hook is None and bool(HOOK_VIDEO_MODEL))
        ) and image_source != "stock" and render_motion_clip_cap > 0
        video_model = (
            HOOK_VIDEO_MODEL or DEFAULT_HOOK_VIDEO_MODEL
            if use_video_hook is True
            else HOOK_VIDEO_MODEL
        )
        if will_use_video_motion:
            max_motion_cost = (
                render_motion_clip_cap * VIDEO_CLIP_ESTIMATED_COST_USD
            )
            logger.info(
                "[Cost] Motion cap=%d clip(s); estimated max motion $%.2f, "
                "estimated max video total $%.2f",
                render_motion_clip_cap,
                max_motion_cost,
                planned_still_cost + max_motion_cost,
            )
        # Step 4: Generate body images
        if use_scenes:
            logger.info("Step 4: Building documentary image edit...")
            themed_images = generate_referent_scene_images(
                body_shots,
                color_intensity=color_intensity,
                visual_sources_out=visual_sources_out,
                article_title=title,
                hero_image=hero_image,
            )
        elif image_source == "mixed":
            logger.info("Step 4: Generating legacy mixed-source shot images...")
            themed_images = generate_scene_images(
                body_shots,
                style_key,
                image_source=image_source,
                series_lane=series_lane,
                hero_image=hero_image,
                color_intensity=color_intensity,
            )
        else:
            logger.info("Step 4: Generating themed images (legacy)...")
            themed_images = generate_themed_images(
                title,
                script,
                num_images=NUM_BODY_IMAGES,
                image_source=image_source,
                color_intensity=color_intensity,
            )
        themed_images = apply_color_intensity_to_images(
            themed_images,
            color_intensity,
        )

        # Step 5: Hook clips (AI video hook or rapid-fire image sequence)
        logger.info("Step 5: Creating hook sequence...")
        opening_visual = scenes[0].get("visual") if use_scenes else None
        hook_opening_image = themed_images[0] if themed_images else None
        hook_opening_images = []
        if use_scenes and themed_images:
            seen_scenes = set()
            seen_sources = set()
            for slot, shot in enumerate(body_shots):
                if slot >= len(themed_images):
                    break
                scene_index = int(shot.get("_scene_index", slot))
                if scene_index in seen_scenes:
                    continue
                seen_scenes.add(scene_index)
                source_records = visual_sources_out or []
                source = (
                    source_records[scene_index]
                    if scene_index < len(source_records)
                    else {}
                )
                source_key = (
                    str(source.get("source_url") or ""),
                    str(source.get("secondary_source_url") or ""),
                )
                if source_key != ("", "") and source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                hook_opening_images.append(themed_images[slot])
                if len(hook_opening_images) >= NUM_HOOK_IMAGES:
                    break
            if not hook_opening_images:
                hook_opening_images = [themed_images[0]]

        hook_clips = create_hook_clips(
            title,
            duration=hook_len,
            image_source=image_source,
            style_key=style_key,
            opening_visual=opening_visual,
            use_video_hook=(
                False if use_scenes else (
                    use_video_hook if render_motion_clip_cap > 0 else False
                )
            ),
            color_intensity=color_intensity,
            opening_image=(hook_opening_image if use_scenes else None),
            opening_images=(hook_opening_images if use_scenes else None),
        )
        clips.extend(hook_clips)
        logger.info(f"Hook: {hook_len:.1f}s")

        # Step 6: Body clips
        logger.info("Step 6: Creating body clips...")

        if use_scenes:
            durations = [float(shot["_duration"]) for shot in body_shots]
            generated_hook_count = sum(
                1 for clip in hook_clips if getattr(clip, "_scap_temp_path", None)
            )
            remaining_motion_slots = max(
                0, render_motion_clip_cap - generated_hook_count
            )
            body_motion_clips = {}
            if will_use_video_motion and video_model and remaining_motion_slots:
                body_motion_clips = create_body_motion_clips(
                    body_shots,
                    durations,
                    style_key,
                    video_model,
                    remaining_motion_slots,
                    color_intensity=color_intensity,
                )
            for i, shot in enumerate(body_shots):
                img = themed_images[i] if i < len(themed_images) else themed_images[-1]
                dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
                clips.append(
                    body_motion_clips.get(i)
                    or create_clip(
                        img,
                        dur,
                        zoom_factor=0.045,
                        motion=SHOT_MOTIONS[i % len(SHOT_MOTIONS)],
                    )
                )
        else:
            for i, shot in enumerate(body_shots):
                img = themed_images[i % len(themed_images)]
                dur = float(shot["_duration"])
                clips.append(
                    create_clip(
                        img,
                        dur,
                        zoom_factor=0.045,
                        motion=SHOT_MOTIONS[i % len(SHOT_MOTIONS)],
                    )
                )

        # Step 7: Assemble visuals and PIL text overlays
        logger.info("Step 7: Assembling...")
        main_video = concatenate_videoclips(clips, method="compose")

        if main_video.duration > audio_duration:
            main_video = main_video.subclip(0, audio_duration)
        elif main_video.duration < audio_duration:
            pad = audio_duration - main_video.duration
            pad_clips = create_final_padding_clips(main_video, pad)
            clips.extend(pad_clips)
            main_video = concatenate_videoclips(
                [main_video, *pad_clips],
                method="compose",
            )

        longest_visual_shot = max(
            (
                float(getattr(clip, "duration", 0.0) or 0.0)
                for clip in clips
            ),
            default=0.0,
        )
        if longest_visual_shot > MAX_SHOT_DURATION + 1e-7:
            raise RuntimeError(
                f"Visual shot exceeded {MAX_SHOT_DURATION:.1f}s cap: "
                f"{longest_visual_shot:.6f}s"
            )
        logger.info(
            "[Pacing] Longest final visual shot %.3fs (cap %.3fs)",
            longest_visual_shot,
            MAX_SHOT_DURATION,
        )

        headline_clip = create_headline_clip(
            title,
            min(HEADLINE_DURATION, audio_duration),
            cover_line=cover_line,
        )
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

        final_audio = audio
        if music_enabled:
            final_audio, music_resources = create_music_mix(
                narration_audio=audio,
                timed_words=timed_words,
                duration=audio_duration,
                article_id=article_id,
            )
        main_video = main_video.set_audio(final_audio)
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
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove partial video output: %s", output_path)
        logger.error(f"Video generation failed: {e}", exc_info=True)
        raise

    finally:
        for resource in reversed(music_resources):
            try:
                resource.close()
            except Exception:
                pass
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
            parent_clip = getattr(c, "_scap_parent_clip", None)
            if parent_clip:
                try:
                    parent_clip.close()
                except Exception:
                    pass
            temp_path = getattr(c, "_scap_temp_path", None)
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
        audio_paths_to_remove = {temp_audio_path}
        if actual_audio_path:
            audio_paths_to_remove.add(Path(actual_audio_path))
        for audio_path in audio_paths_to_remove:
            try:
                audio_path.unlink(missing_ok=True)
            except OSError:
                pass

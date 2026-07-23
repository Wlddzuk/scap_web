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
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from moviepy.audio.fx import all as afx
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
try:
    VIDEO_CLIP_ESTIMATED_COST_USD = max(
        0.0, float(os.getenv("VIDEO_CLIP_ESTIMATED_COST_USD", "0.18"))
    )
except (TypeError, ValueError):
    VIDEO_CLIP_ESTIMATED_COST_USD = 0.18

BASE_VIDEO_ESTIMATED_COST_USD = 0.05
MAX_VIDEO_ESTIMATED_COST_USD = 0.60
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
                },
                timeout=FAL_IMAGE_TIMEOUT_SECONDS,
                start_timeout=min(30, FAL_IMAGE_TIMEOUT_SECONDS),
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


def _parallel_image_gen(prompts: list) -> list:
    """Generate N images in parallel. Returns list[PIL.Image] in prompt order."""
    images = [None] * len(prompts)
    with ThreadPoolExecutor(max_workers=MAX_IMAGE_WORKERS) as executor:
        future_to_idx = {
            executor.submit(generate_image_fal, p): i
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


def generate_scene_images(scenes: list, style_key: str, image_source: str = "ai") -> list:
    """Generate one image per scene, style applied consistently.

    Stock mode queries Pexels with each scene's visual description instead of
    generating with FAL, so cheap test runs stay scene-aligned too.
    """
    if image_source == "stock":
        logger.info(f"[Video] Fetching {len(scenes)} scene-aligned stock photos...")
        images = []
        for scene in scenes:
            query = " ".join((scene.get("visual") or scene.get("speech") or "").split()[:6])
            found = search_pexels_images(query or "science", 1)
            images.append(found[0] if found else create_gradient_background())
        return images

    from visual_styles import apply_style

    prompts = [apply_style(s.get("visual", ""), style_key, is_hook=False) for s in scenes]
    logger.info(f"[Video] Generating {len(prompts)} scene images in style '{style_key}'...")
    return _parallel_image_gen(prompts)


def create_hook_clips(
    title: str,
    duration: float = HOOK_DURATION,
    image_source: str = "ai",
    style_key: str = None,
    opening_visual: str = None,
    use_video_hook: bool | None = None,
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

        local_mp4 = generate_hook_video_fal(video_prompt, video_model)
        if local_mp4:
            try:
                vclip = load_hook_video_clip(local_mp4, duration)
                # Stash temp path on the clip so generate_video's finally can unlink it.
                vclip._scap_temp_path = local_mp4
                logger.info(f"[Hook] Using AI video hook ({duration:.1f}s)")
                return [vclip]
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

        if style_key:
            from visual_styles import apply_style
            hook_prompts = [apply_style(v, style_key, is_hook=True) for v in angle_variations]
        else:
            # Legacy path (no style): use old generic punch prompts
            hook_prompts = [
                f"extreme macro close-up shot, {title}, ultra sharp detail, dramatic rim lighting, shallow depth of field, cinematic 9:16, hyper-realistic",
                f"impossible camera angle, {title}, bird's eye view mixed with dutch angle, dramatic shadows, high contrast neon accents, surreal perspective",
                f"frozen action moment, {title}, motion blur trails, dynamic energy, explosive composition, vibrant saturated colors, dramatic backlighting",
                f"bold graphic composition, {title}, stark contrast, complementary color explosion, minimalist but striking, professional advertising quality"
            ]

        logger.info(f"[Hook] Creating {NUM_HOOK_IMAGES} hook images in parallel (style: {style_key or 'legacy'})...")
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
        if index == 0:
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
) -> dict:
    """Generate selected body motion clips, returning ``{scene_index: clip}``.

    Every failure is represented by an absent dict entry; callers retain their
    already-generated still and Ken Burns fallback.
    """
    indexes = select_motion_scene_indexes(scenes, clip_limit)
    if not indexes:
        return {}

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

        local_mp4 = generate_motion_video_fal(
            motion_prompt,
            video_model,
            log_label=f"BodyVideo:{index}",
        )
        if not local_mp4:
            return index, None
        try:
            target_duration = durations[index] if index < len(durations) else DEFAULT_CHUNK_DURATION
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
) -> str:
    """Generate TikTok-style video with parallel image generation.

    Preferred path: scenes + style_key + emotion provided (from summarizer).
    Each scene produces one style-consistent image, and images play in
    narrative order for their scene's proportional speech duration.
    `emotion` drives TTS voice/speed (Kokoro) and delivery styling (Gemini).

    Fallback path: no scenes -> legacy themed-image generation with
    chunked text pacing.

    Args:
        image_source: 'ai' for FAL.ai, 'stock' for Pexels stock photos.
        captions: Burn word-synced captions into the video when True.
        use_video_hook: True forces the AI video hook, False forces stills,
            None follows the HOOK_VIDEO_MODEL env default.
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
    music_resources = []
    actual_audio_path = None
    use_scenes = bool(scenes)

    try:
        logger.info(
            f"Generating video for article {article_id} "
            f"(images: {image_source}, mode: {'scene-based' if use_scenes else 'legacy'}, "
            f"style: {style_key or 'auto'}, emotion: {emotion or 'default'})"
        )

        # Step 1: TTS
        logger.info("Step 1: Generating voiceover...")
        narration_text = clean_text(script)
        actual_audio_path = tts_engine.synthesize(
            narration_text, str(temp_audio_path), emotion=emotion
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

        # Step 3: Resolve visual style. Always resolve when the video hook is on
        # (legacy path included) so the AI video clip is generated with the SAME
        # style preset as the body images — otherwise the hook looks alien next
        # to the rest of the video.
        will_use_video_motion = (
            use_video_hook is True
            or (use_video_hook is None and bool(HOOK_VIDEO_MODEL))
        ) and image_source != "stock" and MAX_VIDEO_CLIPS_PER_VIDEO > 0
        video_model = (
            HOOK_VIDEO_MODEL or DEFAULT_HOOK_VIDEO_MODEL
            if use_video_hook is True
            else HOOK_VIDEO_MODEL
        )
        if will_use_video_motion:
            max_motion_cost = MAX_VIDEO_CLIPS_PER_VIDEO * VIDEO_CLIP_ESTIMATED_COST_USD
            logger.info(
                "[Cost] Motion cap=%d clip(s); estimated max motion $%.2f, "
                "estimated max video total $%.2f",
                MAX_VIDEO_CLIPS_PER_VIDEO,
                max_motion_cost,
                BASE_VIDEO_ESTIMATED_COST_USD + max_motion_cost,
            )
        if (use_scenes or will_use_video_motion) and not style_key:
            from visual_styles import auto_pick_style
            style_key = auto_pick_style(title, script)
            logger.info(f"[Video] Auto-picked style: {style_key}")

        # Step 4: Generate body images
        if use_scenes:
            logger.info("Step 4: Generating scene-aligned images...")
            themed_images = generate_scene_images(scenes, style_key, image_source=image_source)
        else:
            logger.info("Step 4: Generating themed images (legacy)...")
            themed_images = generate_themed_images(title, script, num_images=NUM_BODY_IMAGES, image_source=image_source)

        # Step 5: Hook clips (AI video hook or rapid-fire image sequence)
        logger.info("Step 5: Creating hook sequence...")
        opening_visual = scenes[0].get("visual") if use_scenes else None
        hook_clips = create_hook_clips(
            title,
            duration=hook_len,
            image_source=image_source,
            style_key=style_key,
            opening_visual=opening_visual,
            use_video_hook=(use_video_hook if MAX_VIDEO_CLIPS_PER_VIDEO > 0 else False),
        )
        clips.extend(hook_clips)
        logger.info(f"Hook: {hook_len:.1f}s")

        # Step 6: Body clips
        logger.info("Step 6: Creating body clips...")
        remaining = max(0.1, audio_duration - hook_len)

        if use_scenes:
            durations = compute_scene_durations(scenes, remaining)
            generated_hook_count = sum(
                1 for clip in hook_clips if getattr(clip, "_scap_temp_path", None)
            )
            remaining_motion_slots = max(
                0, MAX_VIDEO_CLIPS_PER_VIDEO - generated_hook_count
            )
            body_motion_clips = {}
            if will_use_video_motion and video_model and remaining_motion_slots:
                body_motion_clips = create_body_motion_clips(
                    scenes,
                    durations,
                    style_key,
                    video_model,
                    remaining_motion_slots,
                )
            for i, scene in enumerate(scenes):
                img = themed_images[i] if i < len(themed_images) else themed_images[-1]
                dur = durations[i] if i < len(durations) else DEFAULT_CHUNK_DURATION
                clips.append(body_motion_clips.get(i) or create_clip(img, dur))
        else:
            chunks = chunk_text(script)
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
            temp_path = getattr(c, "_scap_temp_path", None)
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except OSError:
                    pass
        if actual_audio_path:
            try:
                Path(actual_audio_path).unlink(missing_ok=True)
            except OSError:
                pass

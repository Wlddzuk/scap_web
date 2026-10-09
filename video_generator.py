"""Pixel Night Lab video renderer: narration, word-synced captions, Moss, music.

One look only (docs/style-lock/STYLE.md). Pipeline:
    1. TTS narration, then word timings (captions, Moss's reactions, music ducking)
    2. Scene shot plan cut on each scene's first spoken word (word-count split
       without timings), capped at MAX_SHOT_DURATION
    3. One pixel scene image per scene (pixel_scenes.py)
    4. Hook cuts, body shots with a push/pan, then Moss (moss_sprite.py) over the
       picture and captions over everything
    5. Ducked music bed and H.264/AAC encode
"""

import functools
import logging
import math
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from groq import Groq
import numpy as np
from moviepy.editor import (
    AudioFileClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    concatenate_videoclips,
    vfx,
)
from moviepy.audio.fx import all as afx
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat
import requests
from dotenv import load_dotenv
import pixel_grid
import tts_engine
from visual_styles import strip_lettering_requests

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


# Video settings
VIDEO_WIDTH = 1080


VIDEO_HEIGHT = 1920

FPS = 30

# Hook settings
HOOK_DURATION = 5.0


NUM_HOOK_IMAGES = 4

FAL_IMAGE_TIMEOUT_SECONDS = _bounded_int_env(
    "FAL_IMAGE_TIMEOUT_SECONDS", 120, 30, 900
)

# Z-Image turbo ($0.005/MP vs schnell's $0.003) won a side-by-side on real
# scene prompts: clearer subjects than schnell, and unlike FLUX.2 flash (same
# price) it does not print proper names from the prompt ("Beta Pictoris b",
# researcher names) as lettering under the burned-in captions.
FAL_IMAGE_MODEL = (
    os.getenv("FAL_IMAGE_MODEL", "fal-ai/z-image/turbo").strip()
    or "fal-ai/z-image/turbo"
)


# The step override only suits FLUX.1 schnell; newer models use their own tuned
# defaults and may reject or degrade under a forced 4-step run.
FAL_IMAGE_STEPS = (
    _bounded_int_env("FAL_IMAGE_STEPS", 4, 1, 100)
    if "flux/schnell" in FAL_IMAGE_MODEL
    else None
)


# Parallel image generation workers
MAX_IMAGE_WORKERS = 6


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

# A 4.5% move over 2.5s is below what a phone viewer perceives as motion.
BODY_SHOT_ZOOM = 0.10


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
        f"no text, no letters, no numbers, no labels, no captions"
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
                if script_text:
                    words = align_words_to_script(words, script_text)
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


# Whisper emits the tail of "calcium-aluminum-rich" or "200,000" as separate
# words ("-rich", ",000"); they start with a joiner and no space.
_CONTINUATION_TOKEN = re.compile(r"^[-‐-–,.'’](?=\w)")


# Script/transcript disagreements no larger than this are respelled from the
# script; anything bigger is a real divergence and keeps Whisper's words.
_ALIGN_MAX_SPAN = 4


def _merge_continuation_words(words: list) -> list:
    """Join word fragments that Whisper split at a hyphen, comma or apostrophe."""
    merged = []
    for word in words:
        text = str(word.get("text", ""))
        if merged and _CONTINUATION_TOKEN.match(text):
            previous = merged[-1]
            merged[-1] = {
                **previous,
                "text": previous["text"] + text,
                "end": max(float(previous["end"]), float(word["end"])),
            }
        else:
            merged.append(dict(word))
    return merged


def _alignment_key(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", str(text).lower())


def _spread_tokens(tokens: list, start: float, end: float) -> list:
    """Time ``tokens`` across [start, end] in proportion to their length."""
    weights = [max(1, len(_alignment_key(token))) for token in tokens]
    total = float(sum(weights))
    span = max(0.0, end - start)
    timed = []
    cursor = start
    for token, weight in zip(tokens, weights):
        token_end = cursor + span * weight / total
        timed.append({"text": token, "start": cursor, "end": max(cursor + 0.05, token_end)})
        cursor = token_end
    return timed


def align_words_to_script(words: list, script_text: str) -> list:
    """Respell Whisper's timed words with the narration script's own words.

    The narration is synthesized from the script, so the script is the true
    text; Whisper only contributes timing. Misheard words ("micro -testless"
    for "microteslas") and split numbers (",000") are replaced by the script
    token over the same time span. Large disagreements keep Whisper's output
    rather than guess.
    """
    words = _merge_continuation_words(words)
    script_tokens = [token for token in str(script_text).split() if _alignment_key(token)]
    if not words or not script_tokens:
        return words

    from difflib import SequenceMatcher

    heard = [_alignment_key(word["text"]) for word in words]
    written = [_alignment_key(token) for token in script_tokens]
    aligned = []
    matcher = SequenceMatcher(None, heard, written, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                aligned.append({**words[i1 + offset], "text": script_tokens[j1 + offset]})
        elif tag == "replace" and max(i2 - i1, j2 - j1) <= _ALIGN_MAX_SPAN:
            aligned.extend(_spread_tokens(
                script_tokens[j1:j2], float(words[i1]["start"]), float(words[i2 - 1]["end"])
            ))
        elif tag == "delete" and i2 - i1 <= 2:
            continue  # words Whisper heard that the narration never said
        elif tag == "insert" and j2 - j1 <= 3:
            gap_start = float(aligned[-1]["end"]) if aligned else 0.0
            gap_end = float(words[i1]["start"]) if i1 < len(words) else gap_start
            if gap_end - gap_start >= 0.08 * (j2 - j1):
                aligned.extend(_spread_tokens(script_tokens[j1:j2], gap_start, gap_end))
        else:
            aligned.extend(dict(word) for word in words[i1:i2])
    return aligned


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


def get_groq_client():
    """Get Groq client if API key exists."""
    api_key = os.getenv("GROQ_API_KEY")
    return Groq(api_key=api_key) if api_key else None


@functools.lru_cache(maxsize=4)
def _grade_alpha_mask(width: int, height: int) -> Image.Image:
    """Vertical alpha ramp darkening the headline and caption zones smoothly."""
    y = np.arange(height, dtype=np.float32) * (1920.0 / max(1, height))
    top = 46.0 * np.clip(1.0 - y / 360.0, 0.0, 1.0) ** 1.5
    bottom = 70.0 * np.clip((y - 1250.0) / (1920.0 - 1250.0), 0.0, 1.0) ** 1.5
    column = np.maximum(top, bottom).astype(np.uint8)
    return Image.fromarray(np.repeat(column[:, None], width, axis=1), mode="L")


def create_clip(
    image: Image.Image,
    duration: float,
    zoom_factor: float = 0.03,
    motion: str = "push",
) -> VideoClip:
    """Create a smooth varied Ken Burns move with no exposed frame edges.

    A gridded Pixel Night Lab shot moves in whole pixels on 12 fps steps instead.
    """
    grid = pixel_grid.grid_of(image)
    if grid is not None:
        low, anchor = grid
        duration = max(0.05, float(duration))
        motion = motion if motion in SHOT_MOTIONS else "push"
        return VideoClip(
            make_frame=lambda t: pixel_grid.frame_at(low, t, duration, motion, anchor),
            duration=duration,
        )
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


MIN_SCENE_DURATION = 0.3


def scene_speech_starts(scenes: list, timed_words: list) -> list | None:
    """When each scene's first word is spoken, from aligned word timings.

    Word counts ignore sentence pauses and the narrator's lead-in, so a
    word-proportional split drifts ahead of the voice (a second or more by
    mid-video). Each scene's tokens are matched against the timed words; a
    scene starts at its first matched word. Returns None when the timings
    can't anchor the scenes, so callers fall back to the word-count split.
    """
    if not scenes or not timed_words:
        return None

    from difflib import SequenceMatcher

    tokens, token_scene = [], []
    for scene_index, scene in enumerate(scenes):
        for token in str(scene.get("speech") or "").split():
            key = _alignment_key(token)
            if key:
                tokens.append(key)
                token_scene.append(scene_index)
    heard = [_alignment_key(word.get("text", "")) for word in timed_words]

    first_heard: dict[int, float] = {}
    matched = 0
    matcher = SequenceMatcher(None, tokens, heard, autojunk=False)
    for block in matcher.get_matching_blocks():
        matched += block.size
        for offset in range(block.size):
            scene_index = token_scene[block.a + offset]
            if scene_index not in first_heard:
                first_heard[scene_index] = float(timed_words[block.b + offset]["start"])
    if not tokens or matched < 0.6 * len(tokens):
        return None

    starts = [0.0]
    for scene_index in range(1, len(scenes)):
        start = first_heard.get(scene_index)
        if start is None or start < starts[-1]:
            return None
        starts.append(start)
    return starts


def compute_timed_scene_durations(
    scenes: list, total_time: float, timed_words: list | None = None,
) -> list:
    """Scene durations that cut on each scene's first spoken word.

    Falls back to the word-count split when timings are missing or unusable.
    Durations always sum to ``total_time``.
    """
    starts = scene_speech_starts(scenes, timed_words or [])
    if starts is None:
        return compute_scene_durations(scenes, total_time)
    # Keep every scene on screen long enough to register without shifting the
    # cuts that follow it.
    for index in range(1, len(starts)):
        starts[index] = max(starts[index], starts[index - 1] + MIN_SCENE_DURATION)
    if starts[-1] > total_time - MIN_SCENE_DURATION:
        return compute_scene_durations(scenes, total_time)
    ends = starts[1:] + [float(total_time)]
    return [end - start for start, end in zip(starts, ends)]


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


def build_scene_shot_plan(
    scenes: list, total_time: float, timed_words: list | None = None,
) -> list:
    """Expand narration scenes into deterministic, shot-capped visual beats.

    With ``timed_words`` each scene's first shot starts when its first word is
    spoken; without them scenes are sized by word count.
    """
    plan = []
    scene_durations = compute_timed_scene_durations(scenes, total_time, timed_words)
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


def split_plan_at_hook(plan: list, hook_len: float) -> list:
    """Return the part of a shot plan that plays after the hook, with its slots.

    The picture track is planned across the FULL narration so every shot lines
    up with the words that sized it. The hook then covers ``[0, hook_len)``, so
    the body must resume with whichever shot is on screen at ``hook_len``,
    trimmed to the time it has left. Planning the body across
    ``audio_duration - hook_len`` instead is what made every image start
    ``hook_len * (1 - f)`` seconds after its own narration.

    Returns ``(slot, shot)`` pairs. ``slot`` is the index into the full plan, so
    callers can still look the shot's image up in a list built from that plan.
    """
    remaining = []
    elapsed = 0.0
    for slot, shot in enumerate(plan):
        duration = float(shot["_duration"])
        end = elapsed + duration
        if end > hook_len + 1e-9:
            trimmed = dict(shot)
            trimmed["_duration"] = min(duration, end - hook_len)
            remaining.append((slot, trimmed))
        elapsed = end
    if not remaining and plan:
        # A hook longer than the whole plan should still leave one shot to hold.
        remaining = [(len(plan) - 1, dict(plan[-1]))]
    return remaining


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



def is_usable_frame(image: Image.Image) -> bool:
    """Reject empty frames: a flat gradient fallback or a near-uniform image."""
    sample = image.convert("L")
    sample.thumbnail((96, 96), Image.Resampling.BILINEAR)
    contrast = float(ImageStat.Stat(sample).stddev[0])
    edges = sample.filter(ImageFilter.FIND_EDGES)
    if edges.width > 4 and edges.height > 4:
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))
    return contrast >= 20.0 or float(ImageStat.Stat(edges).mean[0]) >= 5.0


def shot_variant(image: Image.Image, variant_index: int, framings: tuple) -> Image.Image:
    """A distinct framing of one scene image, so consecutive shots never freeze."""
    source = resize_and_crop_image(image.convert("RGB"), VIDEO_WIDTH, VIDEO_HEIGHT)
    scale, (anchor_x, anchor_y) = framings[variant_index % len(framings)]
    width = max(VIDEO_WIDTH, int(round(VIDEO_WIDTH * scale)))
    height = max(VIDEO_HEIGHT, int(round(VIDEO_HEIGHT * scale)))
    enlarged = source.resize((width, height), Image.Resampling.LANCZOS)
    left = int(round(max(0, width - VIDEO_WIDTH) * anchor_x))
    top = int(round(max(0, height - VIDEO_HEIGHT) * anchor_y))
    edited = enlarged.crop((left, top, left + VIDEO_WIDTH, top + VIDEO_HEIGHT))
    edited = ImageEnhance.Contrast(edited).enhance(1.06)
    # Ramped, not solid: hard-edged bands read as letterbox bars on a phone.
    rgba = edited.convert("RGBA")
    overlay = Image.new("RGBA", rgba.size, (7, 12, 25, 0))
    overlay.putalpha(_grade_alpha_mask(*rgba.size))
    return Image.alpha_composite(rgba, overlay).convert("RGB")


def create_hook_clips(opening_images: list, duration: float) -> list:
    """Rapid cuts across the opening scenes, so the first seconds preview the story."""
    if not opening_images:
        return []
    clip_duration = duration / NUM_HOOK_IMAGES
    return [
        create_clip(
            opening_images[index % len(opening_images)],
            clip_duration,
            zoom_factor=0.038 + (0.006 * (index % 3)),
        )
        for index in range(NUM_HOOK_IMAGES)
    ]


def scene_spans(plan: list) -> list[tuple[float, float]]:
    """(start, end) of each scene in a shot plan, in narration time."""
    spans: dict[int, list[float]] = {}
    elapsed = 0.0
    for shot in plan:
        end = elapsed + float(shot["_duration"])
        index = int(shot.get("_scene_index", len(spans)))
        span = spans.setdefault(index, [elapsed, end])
        span[1] = end
        elapsed = end
    return [tuple(spans[index]) for index in sorted(spans)]


def write_final_video(clip, output_path: Path) -> None:
    """Encode the finished short with the shared H.264/AAC settings."""
    try:
        crf = int(os.getenv("VIDEO_CRF", "26"))
        if not 0 <= crf <= 51:
            raise ValueError
    except ValueError:
        crf = 26
        logger.warning("Invalid VIDEO_CRF; using 26")
    clip.write_videofile(
        str(output_path),
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        audio_bitrate="128k",
        threads=4,
        preset="veryfast",
        ffmpeg_params=["-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        verbose=False,
        logger=None,
    )


def generate_video(
    article_id: int,
    title: str,
    script: str,
    scenes: list | None = None,
    captions: bool = True,
    emotion: str | None = None,
    voice_tone: str = tts_engine.DEFAULT_VOICE_TONE,
    cover_line: str | None = None,
    visual_sources_out: list | None = None,
) -> str:
    """Render one Pixel Night Lab short and return its path under static/videos."""
    import pixel_scenes
    from moss_sprite import create_moss_overlay

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
    if not scenes:
        # Summaries from before the scene contract: one scene per text chunk.
        scenes = [{"speech": chunk, "visual": chunk} for chunk in chunk_text(script)]

    try:
        logger.info(
            "Generating video for article %s (%d scenes, emotion %s, voice %s)",
            article_id, len(scenes), emotion or "default", voice_tone,
        )

        logger.info("Step 1: Generating voiceover...")
        narration_text = clean_text(script)
        actual_audio_path = tts_engine.synthesize(
            narration_text, str(temp_audio_path), emotion=emotion, voice_tone=voice_tone,
        )
        audio = AudioFileClip(actual_audio_path)
        audio_duration = float(audio.duration)
        logger.info("Audio duration: %.1fs", audio_duration)

        # One transcription feeds captions, Moss's reactions and music ducking.
        logger.info("Step 2: Transcribing word timings...")
        timed_words = transcribe_word_timestamps(actual_audio_path, script_text=narration_text)
        caption_groups = group_words_for_captions(timed_words) if captions else []

        hook_len = min(HOOK_DURATION, max(2.0, audio_duration * 0.25))
        full_shots = build_scene_shot_plan(scenes, audio_duration, timed_words)
        body_slots = split_plan_at_hook(full_shots, hook_len)

        logger.info("Step 3: Generating scene images...")
        images = pixel_scenes.generate_scene_images(full_shots, visual_sources_out=visual_sources_out)

        logger.info("Step 4: Cutting hook and body shots...")
        opening, seen = [], set()
        for slot, shot in enumerate(full_shots):
            scene_index = int(shot.get("_scene_index", slot))
            if scene_index not in seen:
                seen.add(scene_index)
                opening.append(images[slot])
            if len(opening) >= NUM_HOOK_IMAGES:
                break
        clips.extend(create_hook_clips(opening, hook_len))
        for slot, shot in body_slots:
            clips.append(create_clip(
                images[slot],
                float(shot["_duration"]),
                zoom_factor=BODY_SHOT_ZOOM,
                motion=SHOT_MOTIONS[slot % len(SHOT_MOTIONS)],
            ))

        logger.info("Step 5: Assembling...")
        main_video = concatenate_videoclips(clips, method="compose")
        if main_video.duration > audio_duration:
            main_video = main_video.subclip(0, audio_duration)
        elif main_video.duration < audio_duration:
            pad_clips = create_final_padding_clips(main_video, audio_duration - main_video.duration)
            clips.extend(pad_clips)
            main_video = concatenate_videoclips([main_video, *pad_clips], method="compose")

        longest = max((float(getattr(clip, "duration", 0.0) or 0.0) for clip in clips), default=0.0)
        if longest > MAX_SHOT_DURATION + 1e-7:
            raise RuntimeError(f"Visual shot exceeded {MAX_SHOT_DURATION:.1f}s cap: {longest:.6f}s")

        headline = create_headline_clip(title, min(HEADLINE_DURATION, audio_duration), cover_line=cover_line)
        if headline:
            overlay_clips.append(headline)
        moss = create_moss_overlay(audio_duration, scene_spans(full_shots), timed_words, hook_len)
        if moss is not None:
            overlay_clips.append(moss)
        if caption_groups:
            overlay_clips.extend(create_caption_clips(caption_groups))
        if overlay_clips:
            base_video = main_video
            main_video = CompositeVideoClip(
                [base_video, *overlay_clips], size=(VIDEO_WIDTH, VIDEO_HEIGHT),
            ).set_duration(audio_duration)

        final_audio = audio
        if _env_flag("MUSIC_ENABLED", True):
            final_audio, music_resources = create_music_mix(
                narration_audio=audio,
                timed_words=timed_words,
                duration=audio_duration,
                article_id=article_id,
            )
        main_video = main_video.set_audio(final_audio)

        logger.info("Step 6: Rendering %.1fs...", main_video.duration)
        write_final_video(main_video, output_path)
        logger.info("Video saved: %s", output_path)
        return str(output_path)

    except Exception as e:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove partial video output: %s", output_path)
        logger.error("Video generation failed: %s", e, exc_info=True)
        raise

    finally:
        for resource in [*reversed(music_resources), audio, main_video, base_video, *overlay_clips, *clips]:
            try:
                if resource is not None:
                    resource.close()
            except Exception:
                pass
        for path in {temp_audio_path, Path(actual_audio_path) if actual_audio_path else None}:
            if path:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

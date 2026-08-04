"""Pluggable text-to-speech engines for Clipper.

The public ``synthesize`` function always returns a WAV path. ``auto`` prefers
Gemini and falls back to the fixed Kokoro voice. Named engines also terminate
at Kokoro so an optional/cloud engine failure does not abort video generation.
"""

import base64
import importlib.util
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import Callable, TypedDict

import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_DEFAULT_VOICE = "Achird"
KOKORO_DEFAULT_VOICE = "af_heart"
QWEN3_DEFAULT_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"
QWEN3_DEFAULT_VOICE = "Ryan"

DEFAULT_VOICE_TONE = "controlled"


class VoiceTonePreset(TypedDict):
    """Voice and delivery settings for one stable narration preset."""

    label: str
    concept: str
    description: str
    gemini_voice: str
    kokoro_voice: str
    kokoro_speed: float
    instruction: str


# Stable narration presets shared by full video generation and the web preview
# controls. ``controlled`` is Clipper's default "Curious Energy" delivery: the
# hook earns attention, the explanation sounds human, and the reveal gets the
# emphasis instead of every sentence being shouted.
VOICE_TONE_PRESETS: dict[str, VoiceTonePreset] = {
    "controlled": {
        "label": "Controlled",
        "concept": "Curious Energy",
        "description": "Strong-curiosity hook, conversational middle, lifted reveal.",
        "gemini_voice": "Achird",
        "kokoro_voice": "af_heart",
        "kokoro_speed": 1.05,
        "instruction": (
            "Use a bright, engaged science-creator voice. Deliver the opening "
            "sentence with strong curiosity and momentum. Then settle into a "
            "natural conversational rhythm. Raise the energy for the main "
            "reveal. Never shout or use a breathless sales cadence. Emphasize "
            "specific facts, names, and numbers. Use natural pauses and "
            "controlled pitch changes."
        ),
    },
    "energetic": {
        "label": "Energetic",
        "concept": "Bright Energy",
        "description": "Faster and brighter, while staying clear and conversational.",
        "gemini_voice": "Puck",
        "kokoro_voice": "af_bella",
        "kokoro_speed": 1.10,
        "instruction": (
            "Use a lively, confident science-creator voice with quick momentum. "
            "Make the opening immediate and make the main reveal land, while "
            "keeping every word clear. Stay human and conversational. Never "
            "shout, squeal, or use a breathless sales cadence. Use brief natural "
            "pauses."
        ),
    },
    "documentary": {
        "label": "Documentary",
        "concept": "Measured Intrigue",
        "description": "Grounded, informative delivery with measured emphasis.",
        "gemini_voice": "Charon",
        "kokoro_voice": "am_michael",
        "kokoro_speed": 1.00,
        "instruction": (
            "Use a clear, grounded documentary voice. Start with quiet intrigue, "
            "explain the evidence conversationally, and add measured emphasis "
            "to the main reveal. Keep the pace steady and confident. Never sound "
            "theatrical, ominous, or breathless."
        ),
    },
}

TTS_STYLE_INSTRUCTION = VOICE_TONE_PRESETS[DEFAULT_VOICE_TONE]["instruction"]

# These are intentionally kept public for the preview tool.
KOKORO_VOICES = [
    "af_heart",   # Grade A - warm female
    "af_bella",   # Grade A- - expressive female
    "af_nicole",  # Grade B- - clear female
    "bf_emma",    # Grade B- - British female
    "am_fenrir",  # Grade C+ - male
    "am_michael", # Grade C+ - professional male
]

# Map dominant emotion -> Kokoro voice that fits the vibe. Values are ordered;
# the first entry is the default pick, extras exist for future rotation/A-B.
# af_nicole is deliberately excluded from the maps — it's whispery/ASMR-leaning
# and kills energy on hook-driven TikTok content.
EMOTION_VOICE_MAP = {
    "shocking":   ["af_bella",  "am_fenrir"],   # expressive, cuts through
    "urgent":     ["am_fenrir", "af_bella"],    # energetic, drives action
    "curious":    ["af_heart",  "bf_emma"],     # warm, inviting
    "triumphant": ["af_bella",  "am_fenrir"],   # bright, confident
    "dark":       ["am_michael","am_fenrir"],   # deeper, serious
    "funny":      ["af_bella",  "af_heart"],    # expressive, playful
}

# Speed nudges per emotion. TikTok pacing is faster than natural speech;
# 1.0 feels sluggish, 1.15 feels energetic. Don't go above 1.2 — Kokoro
# loses articulation.
EMOTION_SPEED_MAP = {
    "shocking":   1.15,
    "urgent":     1.15,
    "curious":    1.10,
    "triumphant": 1.12,
    "dark":       1.05,  # slower for gravitas
    "funny":      1.12,
}
DEFAULT_KOKORO_SPEED = 1.10


def normalize_voice_tone(voice_tone: str | None) -> str:
    """Return a stable preset id, falling back to Curious Energy."""
    key = (voice_tone or DEFAULT_VOICE_TONE).strip().lower()
    if key not in VOICE_TONE_PRESETS:
        logger.warning(
            "[TTS] Unknown voice tone %s; using %s",
            voice_tone,
            DEFAULT_VOICE_TONE,
        )
        return DEFAULT_VOICE_TONE
    return key


def voice_tone_options() -> list[dict[str, str]]:
    """Return browser-safe preset metadata in display order."""
    return [
        {
            "id": key,
            "label": preset["label"],
            "concept": preset["concept"],
            "description": preset["description"],
        }
        for key, preset in VOICE_TONE_PRESETS.items()
    ]


def style_instruction_for_tone(voice_tone: str | None) -> str:
    """Return the delivery direction for a narration preset."""
    key = normalize_voice_tone(voice_tone)
    return VOICE_TONE_PRESETS[key]["instruction"]


def pick_voice_for_tone(voice_tone: str | None) -> tuple[str, float]:
    """Return the Kokoro voice and speed for a narration preset."""
    key = normalize_voice_tone(voice_tone)
    preset = VOICE_TONE_PRESETS[key]
    return preset["kokoro_voice"], preset["kokoro_speed"]


def gemini_voice_for_tone(voice_tone: str | None) -> str:
    """Return the Gemini voice for a narration preset."""
    key = normalize_voice_tone(voice_tone)
    return VOICE_TONE_PRESETS[key]["gemini_voice"]


def pick_voice_for_emotion(emotion: str | None) -> tuple[str, float]:
    """Return (voice, speed) for a given dominant_emotion string.

    Falls back to warm-neutral defaults when emotion is missing or unknown.
    Deterministic: same emotion always yields the same (voice, speed) so
    videos of the same vibe sound consistent across regenerations.

    A KOKORO_VOICE env override always wins over the emotion mapping so a
    channel can keep one fixed brand voice if preferred.
    """
    key = (emotion or "").strip().lower()
    voices = EMOTION_VOICE_MAP.get(key)
    voice = voices[0] if voices else KOKORO_DEFAULT_VOICE
    speed = EMOTION_SPEED_MAP.get(key, DEFAULT_KOKORO_SPEED)
    return voice, speed


# Gemini delivery hints per emotion, appended to the style instruction.
EMOTION_STYLE_HINTS = {
    "shocking":   "Sound genuinely stunned and amazed by what you are revealing.",
    "urgent":     "Sound pressing and immediate, like this cannot wait.",
    "curious":    "Sound intrigued and wondering, pulling the listener into the mystery.",
    "triumphant": "Sound proud and celebratory, like a breakthrough just happened.",
    "dark":       "Sound serious and measured, with quiet gravity.",
    "funny":      "Sound playful and amused, with a smile in your voice.",
}

SUPPORTED_ENGINES = {"auto", "gemini", "kokoro", "qwen3"}
NETWORK_TIMEOUT_SECONDS = 60
RETRY_ATTEMPTS = 2

# Keep indirect Hugging Face model downloads bounded too. These defaults are
# read by huggingface_hub when Kokoro/Qwen are lazily imported.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "10")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

_KOKORO_PIPELINE = None
_KOKORO_LOCK = threading.Lock()
_QWEN3_MODEL = None
_QWEN3_MODEL_NAME = None
_QWEN3_LOCK = threading.Lock()


class TTSUnavailableError(RuntimeError):
    """Raised when an optional TTS engine cannot be used in this environment."""


def _wav_path(output_path: str) -> Path:
    """Normalize any requested output filename to a writable WAV path."""
    path = Path(output_path)
    if path.suffix.lower() != ".wav":
        path = path.with_suffix(".wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _run_with_retry(engine: str, operation: Callable[[], str]) -> str:
    """Run an engine operation twice before allowing selection to fall back."""
    last_error = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return operation()
        except TTSUnavailableError:
            raise
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[TTS:%s] Attempt %d/%d failed: %s",
                engine,
                attempt + 1,
                RETRY_ATTEMPTS,
                exc,
            )
            if attempt + 1 < RETRY_ATTEMPTS:
                time.sleep(1)
    raise RuntimeError(f"{engine} TTS failed after {RETRY_ATTEMPTS} attempts") from last_error


def _write_pcm_wav(path: Path, pcm: bytes) -> str:
    """Wrap raw 24kHz, 16-bit mono PCM bytes in a WAV container."""
    if not pcm:
        raise ValueError("Gemini returned empty audio")
    if len(pcm) % 2:
        raise ValueError("Gemini returned an invalid 16-bit PCM byte count")

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(pcm)
    return str(path)


def _decode_gemini_audio(data) -> bytes:
    """Decode Interactions API base64 audio while tolerating decoded SDK bytes."""
    if isinstance(data, str):
        return base64.b64decode(data, validate=True)
    if isinstance(data, bytes):
        try:
            return base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            return data
    raise TypeError(f"Unsupported Gemini audio payload type: {type(data).__name__}")


def _synthesize_gemini_once(
    text: str,
    output_path: str,
    voice: str | None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key or api_key.lower().startswith("your_"):
        raise TTSUnavailableError("GEMINI_API_KEY is not configured")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise TTSUnavailableError(
            "Gemini TTS requires google-genai; run: pip install -r requirements.txt"
        ) from exc

    selected_voice = (
        voice
        or os.getenv("GEMINI_TTS_VOICE", "").strip()
        or gemini_voice_for_tone(voice_tone)
    )
    style_instruction = style_instruction_for_tone(voice_tone)
    prompt = f"{style_instruction}\n\n{text}"
    client = None
    try:
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=NETWORK_TIMEOUT_SECONDS * 1000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        response = client.interactions.create(
            model=GEMINI_TTS_MODEL,
            input=prompt,
            response_format={"type": "audio"},
            generation_config={
                "speech_config": [{"voice": selected_voice}],
            },
        )
        audio = getattr(response, "output_audio", None)
        data = getattr(audio, "data", None)
        if not data:
            raise ValueError("Gemini response did not contain output_audio.data")

        path = _wav_path(output_path)
        result = _write_pcm_wav(path, _decode_gemini_audio(data))
        logger.info(
            "[TTS] Produced audio with engine=gemini model=%s voice=%s path=%s",
            GEMINI_TTS_MODEL,
            selected_voice,
            result,
        )
        return result
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def synthesize_gemini(
    text: str,
    output_path: str,
    voice: str | None = None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Generate Gemini TTS with a 60s timeout and exactly one caller retry."""
    return _run_with_retry(
        "gemini",
        lambda: _synthesize_gemini_once(
            text,
            output_path,
            voice,
            emotion,
            voice_tone,
        ),
    )


def _get_kokoro_pipeline():
    global _KOKORO_PIPELINE
    if _KOKORO_PIPELINE is None:
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise TTSUnavailableError(
                "Kokoro is required as Clipper's terminal fallback; run: pip install -r requirements.txt"
            ) from exc
        _KOKORO_PIPELINE = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return _KOKORO_PIPELINE


def _synthesize_kokoro_once(
    text: str,
    output_path: str,
    voice: str | None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    import soundfile as sf

    tone_voice, tone_speed = pick_voice_for_tone(voice_tone)
    # Precedence: explicit arg > KOKORO_VOICE env (fixed brand voice) > tone preset.
    selected_voice = (
        voice or os.getenv("KOKORO_VOICE", "").strip() or tone_voice
    )
    if selected_voice not in KOKORO_VOICES:
        logger.warning(
            "[TTS:kokoro] Unknown voice %s; using %s",
            selected_voice,
            tone_voice,
        )
        selected_voice = tone_voice

    with _KOKORO_LOCK:
        pipeline = _get_kokoro_pipeline()
        logger.info(
            "[TTS:kokoro] voice=%s speed=%.2f tone=%s emotion=%s",
            selected_voice,
            tone_speed,
            normalize_voice_tone(voice_tone),
            emotion or "default",
        )
        generator = pipeline(text, voice=selected_voice, speed=tone_speed)
        chunks = [
            np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
            for _gs, _ps, audio_chunk in generator
        ]

    if not chunks:
        raise ValueError("Kokoro returned no audio")
    audio = np.concatenate(chunks)
    path = _wav_path(output_path)
    sf.write(str(path), audio, 24000)
    logger.info(
        "[TTS] Produced audio with engine=kokoro voice=%s path=%s",
        selected_voice,
        path,
    )
    return str(path)


def synthesize_kokoro(
    text: str,
    output_path: str,
    voice: str | None = None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Generate local Kokoro audio with deterministic tone selection."""
    return _run_with_retry(
        "kokoro",
        lambda: _synthesize_kokoro_once(
            text,
            output_path,
            voice,
            emotion,
            voice_tone,
        ),
    )


def _get_qwen3_model(model_name: str):
    global _QWEN3_MODEL, _QWEN3_MODEL_NAME
    if _QWEN3_MODEL is not None and _QWEN3_MODEL_NAME == model_name:
        return _QWEN3_MODEL

    try:
        import torch
        from qwen_tts import Qwen3TTSModel
    except ImportError as exc:
        raise TTSUnavailableError(
            "Qwen3 TTS is optional and not installed; run: pip install -U qwen-tts"
        ) from exc

    logger.warning(
        "[TTS:qwen3] Loading %s on CPU; synthesis can take a few minutes",
        model_name,
    )
    _QWEN3_MODEL = Qwen3TTSModel.from_pretrained(
        model_name,
        device_map="cpu",
        dtype=torch.float32,
        attn_implementation="eager",
    )
    _QWEN3_MODEL_NAME = model_name
    return _QWEN3_MODEL


def _synthesize_qwen3_once(
    text: str,
    output_path: str,
    voice: str | None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    import soundfile as sf

    model_name = os.getenv("QWEN3_TTS_MODEL", QWEN3_DEFAULT_MODEL).strip()
    if "CustomVoice" not in model_name:
        raise ValueError(
            "QWEN3_TTS_MODEL must be a Qwen3-TTS CustomVoice checkpoint so Clipper can use a stable preset voice"
        )

    selected_voice = voice or QWEN3_DEFAULT_VOICE
    instruct = style_instruction_for_tone(voice_tone)
    logger.warning("[TTS:qwen3] CPU synthesis will take a few minutes")
    with _QWEN3_LOCK:
        model = _get_qwen3_model(model_name)
        wavs, sample_rate = model.generate_custom_voice(
            text=text,
            language="English",
            speaker=selected_voice,
            instruct=instruct,
        )

    if not wavs:
        raise ValueError("Qwen3 TTS returned no audio")
    path = _wav_path(output_path)
    sf.write(str(path), wavs[0], sample_rate)
    logger.info(
        "[TTS] Produced audio with engine=qwen3 model=%s voice=%s path=%s",
        model_name,
        selected_voice,
        path,
    )
    return str(path)


def synthesize_qwen3(
    text: str,
    output_path: str,
    voice: str | None = None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Generate optional Qwen3 TTS locally on CPU with one retry."""
    if importlib.util.find_spec("qwen_tts") is None:
        message = "Qwen3 TTS is optional and not installed; run: pip install -U qwen-tts"
        logger.warning("[TTS:qwen3] %s", message)
        raise TTSUnavailableError(message)
    return _run_with_retry(
        "qwen3",
        lambda: _synthesize_qwen3_once(
            text,
            output_path,
            voice,
            emotion,
            voice_tone,
        ),
    )


def engine_availability(engine: str) -> tuple[bool, str]:
    """Report whether an engine can be attempted by the preview tool."""
    engine = engine.strip().lower()
    if engine == "gemini":
        if not os.getenv("GEMINI_API_KEY", "").strip():
            return False, "GEMINI_API_KEY is not configured"
        if importlib.util.find_spec("google.genai") is None:
            return False, "google-genai is not installed"
        return True, "available"
    if engine == "kokoro":
        if importlib.util.find_spec("kokoro") is None:
            return False, "kokoro is not installed"
        return True, "available"
    if engine == "qwen3":
        if importlib.util.find_spec("qwen_tts") is None:
            return False, "qwen-tts is not installed"
        return True, "available"
    return False, f"unknown engine: {engine}"


def default_voice_for_engine(
    engine: str,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Return the configured stable voice used by an engine."""
    engine = engine.strip().lower()
    if engine == "gemini":
        return (
            os.getenv("GEMINI_TTS_VOICE", "").strip()
            or gemini_voice_for_tone(voice_tone)
        )
    if engine == "kokoro":
        tone_voice, _speed = pick_voice_for_tone(voice_tone)
        return os.getenv("KOKORO_VOICE", "").strip() or tone_voice
    if engine == "qwen3":
        return QWEN3_DEFAULT_VOICE
    raise ValueError(f"Unknown TTS engine: {engine}")


def synthesize_with_engine(
    engine: str,
    text: str,
    output_path: str,
    voice: str | None = None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Synthesize with one explicit engine and no cross-engine fallback.

    This is primarily for previews; production callers should use
    :func:`synthesize` so Kokoro remains the terminal fallback.
    """
    engine = engine.strip().lower()
    functions = {
        "gemini": synthesize_gemini,
        "kokoro": synthesize_kokoro,
        "qwen3": synthesize_qwen3,
    }
    if engine not in functions:
        raise ValueError(f"Unknown TTS engine: {engine}")
    return functions[engine](
        text,
        output_path,
        voice,
        emotion,
        voice_tone,
    )


def synthesize(
    text: str,
    output_path: str,
    voice: str | None = None,
    emotion: str | None = None,
    voice_tone: str | None = DEFAULT_VOICE_TONE,
) -> str:
    """Synthesize speech using configured selection with terminal Kokoro fallback.

    `voice_tone` governs narration delivery. `emotion` remains accepted for
    backwards compatibility and visual metadata, but does not override tone.
    """
    configured = os.getenv("TTS_ENGINE", "auto").strip().lower()
    if configured not in SUPPORTED_ENGINES:
        logger.warning("[TTS] Unknown TTS_ENGINE=%s; using auto", configured)
        configured = "auto"

    if configured == "auto":
        engines = ["gemini", "kokoro"]
    elif configured == "kokoro":
        engines = ["kokoro"]
    else:
        engines = [configured, "kokoro"]

    last_error = None
    for index, engine in enumerate(engines):
        try:
            logger.info("[TTS] Trying engine=%s", engine)
            return synthesize_with_engine(
                engine,
                text,
                output_path,
                voice,
                emotion,
                voice_tone,
            )
        except Exception as exc:
            last_error = exc
            if index + 1 < len(engines):
                logger.warning(
                    "[TTS] engine=%s failed (%s); falling back to engine=%s",
                    engine,
                    exc,
                    engines[index + 1],
                )
            else:
                logger.error("[TTS] Terminal engine=%s failed: %s", engine, exc)

    raise RuntimeError("All configured TTS engines failed") from last_error

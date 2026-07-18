"""Render short voice samples for every available Clipper TTS engine."""

import logging
import re
import time
import wave
from pathlib import Path

import tts_engine

logger = logging.getLogger(__name__)

SAMPLE_TEXT = "Plants may be talking right under our noses — and scientists just proved it."
DEFAULT_OUTPUT_DIR = Path("static/tts_previews")


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "_", value.lower()).strip("_")


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnframes() / float(wav_file.getframerate())


def preview_specs() -> list[tuple[str, str]]:
    """Return Gemini/Qwen defaults plus every supported Kokoro voice."""
    return [
        ("gemini", tts_engine.default_voice_for_engine("gemini")),
        *(("kokoro", voice) for voice in tts_engine.KOKORO_VOICES),
        ("qwen3", tts_engine.default_voice_for_engine("qwen3")),
    ]


def render_previews(output_dir: str | Path = DEFAULT_OUTPUT_DIR) -> list[dict]:
    """Render available previews and return structured result rows."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for engine, voice in preview_specs():
        available, detail = tts_engine.engine_availability(engine)
        output_path = output_dir / f"{_safe_name(engine)}_{_safe_name(voice)}.wav"
        started = time.perf_counter()

        if not available:
            results.append(
                {
                    "engine": engine,
                    "voice": voice,
                    "status": "skipped",
                    "duration": None,
                    "generation_time": 0.0,
                    "path": None,
                    "detail": detail,
                }
            )
            continue

        try:
            actual_path = Path(
                tts_engine.synthesize_with_engine(
                    engine,
                    SAMPLE_TEXT,
                    str(output_path),
                    voice=voice,
                )
            )
            elapsed = time.perf_counter() - started
            results.append(
                {
                    "engine": engine,
                    "voice": voice,
                    "status": "ok",
                    "duration": _wav_duration(actual_path),
                    "generation_time": elapsed,
                    "path": str(actual_path),
                    "detail": "",
                }
            )
        except Exception as exc:
            elapsed = time.perf_counter() - started
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass
            logger.warning("Preview failed for %s/%s: %s", engine, voice, exc)
            results.append(
                {
                    "engine": engine,
                    "voice": voice,
                    "status": "failed",
                    "duration": None,
                    "generation_time": elapsed,
                    "path": None,
                    "detail": str(exc),
                }
            )

    return results


def format_results_table(results: list[dict]) -> str:
    """Format preview results without adding a table dependency."""
    headers = ["ENGINE", "VOICE", "STATUS", "DURATION", "GENERATE", "DETAIL"]
    rows = []
    for result in results:
        duration = "-" if result["duration"] is None else f"{result['duration']:.2f}s"
        generation = f"{result['generation_time']:.2f}s"
        detail = result.get("detail", "")[:64]
        rows.append(
            [
                result["engine"],
                result["voice"],
                result["status"],
                duration,
                generation,
                detail,
            ]
        )

    widths = [
        max(len(headers[index]), *(len(str(row[index])) for row in rows))
        for index in range(len(headers))
    ]

    def format_row(row):
        return "  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)).rstrip()

    separator = "  ".join("-" * width for width in widths)
    return "\n".join([format_row(headers), separator, *(format_row(row) for row in rows)])


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    results = render_previews()
    print(format_results_table(results))
    succeeded = sum(result["status"] == "ok" for result in results)
    print(f"\nWrote {succeeded} preview file(s) to {DEFAULT_OUTPUT_DIR}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())

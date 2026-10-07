"""Pixel Night Lab scene images: the channel's single locked look.

Every scene is generated as wordless pixel art by a cheap text-to-image model.
Moss is not painted in; he is animated over the scenes by moss_sprite.py.
Direction and decision history: docs/style-lock/STYLE.md.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

import scene_check
import video_generator as vg
from llm_models import GROQ_EXTRA_BODY, GROQ_TEXT_MODEL

logger = logging.getLogger(__name__)

STYLE_KEY = "pixel_night_lab"

SCENE_IMAGE_MODEL = (
    os.getenv("SCENE_IMAGE_MODEL", "fal-ai/z-image/turbo").strip() or "fal-ai/z-image/turbo"
)
SCENE_IMAGE_COST_USD = vg._bounded_float_env("SCENE_IMAGE_COST_USD", 0.005, 0.0, 10.0)
# Scene images the checker may regenerate per video (each costs one more image).
MAX_SCENE_RETRIES = vg._bounded_int_env("MAX_SCENE_RETRIES", 3, 0, 20)

# The scene model has no negative prompt: every noun it reads, it draws
# ("never planets or rings" produced a ringed planet over a lab bench). Keep
# this prompt positive-only; generate_image_fal appends the no-text suffix.
PIXEL_LOOK = (
    "Chunky 16-bit pixel art with a crisp visible pixel grid. Solid flat deep "
    "navy-blue background filling the whole frame. Neon magenta and amber energy "
    "lines show forces and movement. Secondary objects are dark slate and grey. "
    "The subject is large, bright and placed in the upper half of the frame; the "
    "lower third is empty dark navy."
)
SPACE_SCIENCE = (
    "Magnetic fields are curved dipole loops arcing out of the poles. An early solar "
    "system is a flat protoplanetary disk of dust around a young star."
)
_SPACE_SCENE = re.compile(
    r"\b(space|star|stars|sun|solar|planet|planets|orbit|galaxy|nebula|cosmic|"
    r"meteorite|asteroid|comet|magnetic|magnetism|universe|protostar)\b",
    re.IGNORECASE,
)

# Shot variants zoom toward the TOP of the frame (anchor y near 0): the subject
# lives in the upper half and Moss walks along the bottom of it.
FRAMINGS = (
    (1.0, (0.50, 0.50)),
    (1.16, (0.50, 0.05)),
    (1.10, (0.20, 0.00)),
    (1.16, (0.80, 0.05)),
    (1.10, (0.50, 0.10)),
)

# Older summaries describe scenes as infographics ("timeline bar from day 0 to
# day 28"); image models print those numbers. Such visuals are rewritten into a
# physical, wordless scene first. New summaries are asked for physical visuals.
_INFOGRAPHIC = re.compile(
    r"\b(chart|graph|timeline|bar|bars|axis|axes|label(?:l?ed)?|marked|icon|"
    r"diagram|readout|display|percent|checkmark|arrow labeled|day \d+)\b",
    re.IGNORECASE,
)


def estimate_video_cost(scene_count: int) -> float:
    return max(0, scene_count) * SCENE_IMAGE_COST_USD


def physicalize_visual(visual: str, speech: str = "") -> str:
    """Rewrite an infographic scene description as a wordless physical scene."""
    if not visual or not _INFOGRAPHIC.search(visual):
        return visual
    client = vg.get_groq_client()
    if not client:
        return visual
    try:
        response = client.chat.completions.create(
            model=GROQ_TEXT_MODEL,
            extra_body=GROQ_EXTRA_BODY,
            messages=[
                {"role": "system", "content": (
                    "You rewrite scene descriptions for a pixel-art science video. "
                    "Turn charts, timelines, graphs, labels and icons into one "
                    "physical scene built from the LITERAL scientific objects in the "
                    "narration (cells, molecules, organs, rocks, planets, lab "
                    "instruments). Never substitute metaphors such as plants, trees, "
                    "buildings, landscapes or everyday objects. Show quantities as "
                    "counts or sizes of those objects and time as the objects "
                    "changing. Do not mention any numbers, dates or units, not even "
                    "as words. No people, no hands, no text, labels, charts or "
                    "screens. Reply with one complete sentence of at most 35 words "
                    "and nothing else."
                )},
                {"role": "user", "content": f"NARRATION: {speech}\nSCENE: {visual}"},
            ],
            temperature=0.4,
            # gpt-oss spends hidden reasoning tokens from this budget first.
            max_tokens=400,
        )
        rewritten = (response.choices[0].message.content or "").strip().strip('"')
        # A sentence without final punctuation ran out of budget mid-thought.
        if rewritten.endswith((".", "!", "?")) and not _INFOGRAPHIC.search(rewritten):
            logger.info("[Pixel] Physicalized scene: %s -> %s", visual[:60], rewritten[:80])
            return rewritten
    except Exception as exc:
        logger.info("[Pixel] Scene rewrite unavailable: %s", exc)
    return visual


def build_scene_prompt(scene: dict) -> str:
    """WHAT comes from the scene; HOW is the locked, positive-only look."""
    subject = vg.strip_lettering_requests(
        str(scene.get("_physical_visual") or scene.get("visual") or scene.get("speech") or "")
    ).rstrip(" .") or "a single glowing science subject"
    parts = [f"{subject}.", PIXEL_LOOK]
    if _SPACE_SCENE.search(subject):
        parts.append(SPACE_SCIENCE)
    return " ".join(parts)


def check_and_retry(scenes: list, images: list) -> dict:
    """Check each generated image; regenerate a failed scene once and keep the better image.

    Mutates ``images`` in place and returns {scene index: record fields} for the
    visual_sources log. Retries are capped per video so a bad prompt can't run up cost.
    """
    if not scene_check.enabled():
        return {}
    todo = [index for index, image in enumerate(images) if image is not None]
    visual = lambda index: str(scenes[index].get("_physical_visual") or scenes[index].get("visual") or "")
    with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
        verdicts = dict(zip(todo, executor.map(lambda i: scene_check.check_scene_image(images[i], visual(i)), todo)))

    failed = [index for index in todo if not verdicts[index].passed][:MAX_SCENE_RETRIES]

    def retry(index):
        try:
            image = vg.generate_image_fal(build_scene_prompt(scenes[index]), model=SCENE_IMAGE_MODEL, num_inference_steps=None)
        except Exception as exc:
            logger.info("[SceneCheck] Retry of scene %d failed: %s", index, exc)
            return None, None
        if image is None or not vg.is_usable_frame(image):
            return None, None
        return image, scene_check.check_scene_image(image, visual(index))

    retried = {}
    if failed:
        with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
            retried = dict(zip(failed, executor.map(retry, failed)))

    out = {}
    for index, verdict in verdicts.items():
        record = {"check": verdict.issues or ["pass"]} if verdict.checked else {}
        image, second = retried.get(index, (None, None))
        if image is not None:
            kept_retry = second.score < verdict.score
            if kept_retry:
                images[index] = image
            record.update(retry=second.issues or ["pass"], kept="retry" if kept_retry else "first")
        if verdict.checked and not verdict.passed:
            logger.info("[SceneCheck] Scene %d %s: %s -> %s", index, verdict.issues, verdict.note,
                        record.get("kept", "no retry"))
        out[index] = record
    logger.info("[SceneCheck] %d checked, %d failed, %d regenerated", len(verdicts), len(failed), len(retried))
    return out


def generate_scene_images(shots: list, *, visual_sources_out: list | None = None) -> list:
    """Return one pixel frame per shot, in shot order (one image per scene)."""
    unique_positions, slot_to_unique, scene_to_unique = [], [], {}
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
    logger.info(
        "[Pixel] %d scenes via %s, est $%.2f",
        len(scenes), SCENE_IMAGE_MODEL, estimate_video_cost(len(scenes)),
    )

    with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
        rewritten = list(executor.map(
            lambda scene: physicalize_visual(str(scene.get("visual") or ""), str(scene.get("speech") or "")),
            scenes,
        ))
    scenes = [{**scene, "_physical_visual": visual} for scene, visual in zip(scenes, rewritten)]

    images: list[Image.Image | None] = [None] * len(scenes)
    if os.getenv("FAL_KEY"):
        with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
            futures = {
                executor.submit(
                    vg.generate_image_fal,
                    build_scene_prompt(scene),
                    model=SCENE_IMAGE_MODEL,
                    num_inference_steps=None,
                ): index
                for index, scene in enumerate(scenes)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    image = future.result()
                except Exception as exc:
                    logger.info("[Pixel] Scene %d failed: %s", index, exc)
                    image = None
                # generate_image_fal returns a flat gradient on failure.
                images[index] = image if image is not None and vg.is_usable_frame(image) else None

    checks = check_and_retry(scenes, images)

    generated = [index for index, image in enumerate(images) if image is not None]
    if not generated:
        raise RuntimeError("No scene image could be generated; refusing to render a blank video")
    records = []
    for index in range(len(images)):
        if images[index] is None:
            # Reuse the nearest generated scene; never a blank card.
            nearest = min(generated, key=lambda other: abs(other - index))
            images[index] = images[nearest]
            records.append({"lane": "pixel", "provider": SCENE_IMAGE_MODEL, "reused_from": nearest})
        else:
            records.append({"lane": "pixel", "provider": SCENE_IMAGE_MODEL, **checks.get(index, {})})
    if visual_sources_out is not None:
        visual_sources_out.extend(records)
    logger.info(
        "[Pixel] generated=%d reused=%d",
        len(generated), len(images) - len(generated),
    )
    return [
        vg.shot_variant(images[unique_index], int((shot or {}).get("_shot_step", slot)), FRAMINGS)
        for slot, (shot, unique_index) in enumerate(zip(shots, slot_to_unique))
    ]

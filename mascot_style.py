"""Pixel Night Lab: the locked channel look, with Moss the tardigrade mascot.

Every scene is generated in one pixel-art world (no archive photos) by an image
*edit* model that receives Moss's approved canonical reference, so the mascot
stays on-model from shot to shot. Moss appears in the hook and in every third
scene; the other scenes show the subject alone in the same style.

Direction, mascot bible and acceptance tests: docs/style-lock/STYLE.md. The
prompt blocks below are the canonical copies; the doc points here.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

import video_generator as vg
from llm_models import GROQ_EXTRA_BODY, GROQ_TEXT_MODEL
from visual_styles import strip_lettering_requests

logger = logging.getLogger(__name__)

MASCOT_DIR = Path(__file__).resolve().parent / "assets" / "moss"
CANONICAL_REFERENCE = MASCOT_DIR / "moss_canonical.png"
# Used on-brand when a scene cannot be generated: Moss on the navy ground is a
# better gap-filler than a gradient card or a frame from an unrelated scene.
FALLBACK_REFERENCE = MASCOT_DIR / "moss_thinking.png"

MASCOT_IMAGE_MODEL = (
    os.getenv("MASCOT_IMAGE_MODEL", "fal-ai/nano-banana/edit").strip()
    or "fal-ai/nano-banana/edit"
)
# Scenes without Moss need no reference (given Moss's image "for style only", the
# edit model still drew him), so they use a cheap text-to-image model instead.
# Z-Image Turbo held the pixel look in a side-by-side test at ~1/8 the cost.
SCENE_IMAGE_MODEL = (
    os.getenv("SCENE_IMAGE_MODEL", "fal-ai/z-image/turbo").strip() or "fal-ai/z-image/turbo"
)
SCENE_IMAGE_COST_USD = vg._bounded_float_env("SCENE_IMAGE_COST_USD", 0.005, 0.0, 10.0)
MASCOT_IMAGE_COST_USD = vg._bounded_float_env("MASCOT_IMAGE_COST_USD", 0.039, 0.0, 10.0)
MOSS_EVERY_N_SCENES = 3

MOSS = (
    "Moss: an original cartoon tardigrade mascot. A plump upright bean-shaped body "
    "with three soft segment creases, standing on short hind feet, two short front "
    "limbs ending in tiny claws, mint-sage green body with a lighter cream belly, "
    "two small black bead eyes, a small round mouth, and brass explorer goggles with "
    "amber lenses. Keep Moss exactly as in the attached reference: same proportions, "
    "colours, creases and goggles."
)
# Every block below except MOSS and MOSS_AVOID goes into EVERY scene, so none
# may mention Moss: when the style block said "Moss is the brightest shape", the
# model wrote "Moss" as text and invented look-alike mascots in Moss-free scenes.
STYLE = (
    "Style: chunky 16-bit pixel art with a crisp visible pixel grid and no "
    "anti-aliasing blur. Solid flat deep navy-blue background, even indoors or "
    "inside the body (never wood, walls or daylight). No checkerboard, no "
    "transparency pattern, no gradient banding. Neon magenta and amber are used only for forces, energy and "
    "highlights. Natural objects keep their real colours; everything else is dark "
    "slate, grey and navy pixel shapes. The main subject is the brightest shape in "
    "the frame and nothing else is added to the scene."
)
CAPTION_BAND = (
    "Composition rule: the horizontal band from 62% to 85% of the frame height must "
    "contain only dark background or plain dark ground: no key objects and no bright "
    "lines there, because captions are overlaid in that band. Keep the subject in "
    "the upper half or the side thirds."
)
QUANTITIES = (
    "Show amounts, comparisons and time as physical things (sizes, heights, counts "
    "of objects, a growing pile), never as a chart, graph, axis, timeline or calendar."
)
AVOID = (
    "Avoid: human hands or arms, realistic microscope texture, split panels, inset "
    "boxes or comic layouts "
    "(one continuous scene only), charts, axes, tick marks, timelines, screens or "
    "displays showing numbers or words, colour codes, any text, letters, numbers, "
    "labels, names, logos, watermark or signature. Vertical 9:16."
)
MOSS_AVOID = (
    "For Moss avoid: teeth, human hands or fingers, clothing besides the goggles, a "
    "second pair of goggles, a glow outline, extra accessories. Moss keeps his "
    "mint-green colour and is the brightest shape in the frame."
)
FORCES = (
    "If the scene involves an invisible force, field, flow, signal or heat, draw it "
    "as neon magenta or amber lines or streams. Keep the science accurate: draw "
    "magnetic fields as curved dipole loops arcing out of the poles, never as a "
    "flat ring around a planet or star (only Saturn has rings); draw an early solar "
    "system as a flat protoplanetary disk, not a spiral galaxy."
)
# The cheap text-to-image model has no negative prompt: every noun it reads, it
# draws ("never planets or rings" produced a ringed planet over a lab bench). Its
# prompt is therefore positive-only; generate_image_fal appends the no-text suffix.
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
NO_MASCOT = "No mascot or cartoon character appears in this scene; show only the subject."
# Shot variants zoom toward the TOP of the frame (anchor y near 0): Moss and the
# subject live in the upper half, and the archive-photo framings cut his head off.
MASCOT_FRAMINGS = (
    (1.0, (0.50, 0.50)),
    (1.16, (0.50, 0.05)),
    (1.10, (0.20, 0.00)),
    (1.16, (0.80, 0.05)),
    (1.10, (0.50, 0.10)),
)
_MOSS_ACTIONS = (
    "Moss leans in, amazed, goggles pulled down over its eyes",
    "Moss watches calmly from the side, goggles up",
    "Moss points at the subject with one claw, curious",
)


def moss_scene_indexes(scene_count: int) -> set[int]:
    """Scenes that feature Moss: the hook, then every third scene."""
    return {index for index in range(scene_count) if index % MOSS_EVERY_N_SCENES == 0}


def estimate_mascot_cost(scene_count: int) -> float:
    """Moss scenes use the reference-aware edit model; the rest the cheap model."""
    moss = len(moss_scene_indexes(scene_count))
    return moss * MASCOT_IMAGE_COST_USD + (scene_count - moss) * SCENE_IMAGE_COST_USD


# The summarizer writes scene visuals for an infographic look ("timeline bar from
# day 0 to day 28", "a badge labeled CD33"). Image models obey those literally and
# print the numbers, whatever the negative prompt says, so such visuals are
# rewritten into a physical, wordless scene before they reach the image model.
_INFOGRAPHIC = re.compile(
    r"\b(chart|graph|timeline|bar|bars|axis|axes|label(?:l?ed)?|marked|icon|"
    r"diagram|readout|display|percent|checkmark|arrow labeled|day \d+)\b",
    re.IGNORECASE,
)


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
            logger.info("[Mascot] Physicalized scene: %s -> %s", visual[:60], rewritten[:80])
            return rewritten
    except Exception as exc:
        logger.info("[Mascot] Scene rewrite unavailable: %s", exc)
    return visual


def build_scene_prompt(scene: dict, *, with_moss: bool, is_hook: bool, turn: int = 0) -> str:
    """WHAT comes from the scene; HOW comes from the locked blocks above."""
    subject = strip_lettering_requests(
        str(scene.get("_physical_visual") or scene.get("visual") or scene.get("speech") or "")
    ).rstrip(" .") or "a single glowing science subject"
    if not with_moss:
        # Positive-only prompt for the cheap model (see PIXEL_LOOK).
        parts = [f"{subject}.", PIXEL_LOOK]
        if _SPACE_SCENE.search(subject):
            parts.append(SPACE_SCIENCE)
        return " ".join(parts)

    parts = [f"Scene: {subject}."]
    # The hook is always the amazed reaction; later appearances alternate.
    action = _MOSS_ACTIONS[0] if is_hook else _MOSS_ACTIONS[1 + turn % 2]
    parts += [f"{action}, small beside the subject.", MOSS]
    parts += [FORCES, QUANTITIES, STYLE, CAPTION_BAND, AVOID, MOSS_AVOID]
    return "\n\n".join(parts)


def _generate_one(prompt: str, reference_url: str | None) -> Image.Image | None:
    """Generate one frame; with a reference it is a Moss scene, without one it is not."""
    if not reference_url:
        # generate_image_fal returns a flat gradient when it fails; the
        # usability gate in the caller turns that into the Moss fallback frame.
        return vg.generate_image_fal(prompt, model=SCENE_IMAGE_MODEL, num_inference_steps=None)

    import fal_client

    arguments = {
        "prompt": prompt,
        "image_urls": [reference_url],
        "aspect_ratio": "9:16",
        "num_images": 1,
        "output_format": "png",
    }
    for attempt in range(vg.RETRY_ATTEMPTS):
        try:
            result = fal_client.run(
                MASCOT_IMAGE_MODEL,
                arguments=arguments,
                timeout=vg.FAL_IMAGE_TIMEOUT_SECONDS,
                start_timeout=min(30, vg.FAL_IMAGE_TIMEOUT_SECONDS),
            )
            url = result["images"][0]["url"]
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            return vg.resize_and_crop_image(image, vg.VIDEO_WIDTH, vg.VIDEO_HEIGHT)
        except Exception as exc:
            logger.info("[Mascot] Scene attempt %d failed: %s", attempt + 1, exc)
    return None


def generate_mascot_scene_images(
    shots: list,
    *,
    article_title: str = "",
    visual_sources_out: list | None = None,
) -> list:
    """Return one Pixel Night Lab frame per shot, in shot order."""
    import fal_client

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
    with_moss = moss_scene_indexes(len(scenes))
    logger.info(
        "[Mascot] %d scenes (%d with Moss via %s, rest via %s), est $%.2f",
        len(scenes), len(with_moss), MASCOT_IMAGE_MODEL, SCENE_IMAGE_MODEL,
        estimate_mascot_cost(len(scenes)),
    )

    with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
        rewritten = list(executor.map(
            lambda scene: physicalize_visual(str(scene.get("visual") or ""), str(scene.get("speech") or "")),
            scenes,
        ))
    scenes = [{**scene, "_physical_visual": visual} for scene, visual in zip(scenes, rewritten)]

    canonical = Image.open(CANONICAL_REFERENCE).convert("RGB")
    images: list[Image.Image | None] = [None] * len(scenes)
    if os.getenv("FAL_KEY"):
        reference_url = fal_client.upload_image(canonical, format="png")
        prompts = [
            build_scene_prompt(
                scene,
                with_moss=index in with_moss,
                is_hook=index == 0,
                turn=index // MOSS_EVERY_N_SCENES,
            )
            for index, scene in enumerate(scenes)
        ]
        with ThreadPoolExecutor(max_workers=vg.MAX_IMAGE_WORKERS) as executor:
            futures = {
                executor.submit(
                    _generate_one, prompt, reference_url if index in with_moss else None
                ): index
                for index, prompt in enumerate(prompts)
            }
            for future in as_completed(futures):
                images[futures[future]] = future.result()
    else:
        logger.info("[Mascot] No FAL_KEY; every scene uses the Moss fallback frame")

    fallback = vg.resize_and_crop_image(
        Image.open(FALLBACK_REFERENCE).convert("RGB"), vg.VIDEO_WIDTH, vg.VIDEO_HEIGHT
    )
    records = []
    for index, image in enumerate(images):
        generated = image is not None and vg._documentary_image_is_usable(image, generated=True)
        if not generated:
            images[index] = fallback
        records.append({
            "lane": "mascot",
            "provider": "FAL" if generated else "Moss reference",
            "model": (MASCOT_IMAGE_MODEL if index in with_moss else SCENE_IMAGE_MODEL)
            if generated else "",
            "source_url": "",
            "license": "Generated illustration" if generated else "SCAP mascot asset",
            "author": "AI generated" if generated else "SCAP",
            "subject_verified": False,
            "verification_method": (
                "pixel night lab scene" if generated else "mascot fallback frame"
            ),
            "moss": index in with_moss,
        })
    if visual_sources_out is not None:
        visual_sources_out.extend(records)
    logger.info(
        "[Mascot] generated=%d fallback=%d",
        sum(record["provider"] == "FAL" for record in records),
        sum(record["provider"] != "FAL" for record in records),
    )
    return [
        vg._documentary_photo_variant(
            images[unique_index],
            shot,
            int((shot or {}).get("_shot_step", slot_index)),
            framings=MASCOT_FRAMINGS,
        )
        for slot_index, (shot, unique_index) in enumerate(zip(shots, slot_to_unique))
    ]

"""Round 2 for the locked Pixel Night Lab route: Moss reference set + story frames.

Run from the repo root: python docs/style-lock/pixel-night-lab/generate_round2.py
Nano Banana via FAL (~$0.039/image). Existing files are skipped, so reruns only
regenerate what was deleted.
"""
import sys
import time
from io import BytesIO
from pathlib import Path

import fal_client
import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

TEXT_MODEL = "fal-ai/nano-banana"
EDIT_MODEL = "fal-ai/nano-banana/edit"
HERE = Path(__file__).resolve().parent
ROUND1_REFERENCE = HERE.parent / "proofs" / "B_pixel_night_lab_0_reference.png"

MOSS = (
    "Moss: an original cartoon tardigrade mascot. A plump upright bean-shaped body "
    "with three soft segment creases, standing on short hind feet, two short front "
    "limbs ending in tiny claws, mint-sage body (#9ED9C3) with a lighter cream belly, "
    "two small black bead eyes, a small round mouth, and brass explorer goggles with "
    "amber lenses. Keep Moss exactly as in the attached reference: same proportions, "
    "colours, creases and goggles."
)
STYLE = (
    "Style: chunky 16-bit pixel art with a crisp visible pixel grid and no "
    "anti-aliasing blur. Solid flat deep navy background (#0B1026) with a few small "
    "twinkling pixel stars. No checkerboard, no transparency pattern, no gradient "
    "banding. Neon magenta (#FF3DA8) and amber (#FFB000) are used only for forces, "
    "energy and highlights. Moss is the brightest shape in the frame."
)
CAPTION_BAND = (
    "Composition rule: the horizontal band from 62% to 85% of the frame height must "
    "contain only dark background or plain dark ground: no Moss, no key objects, no "
    "bright lines there, because captions are overlaid in that band. Put Moss in the "
    "upper half or the side thirds."
)
AVOID = (
    "Avoid: realistic microscope texture, teeth, human hands or fingers, clothing "
    "besides the goggles, extra accessories, any text, letters, numbers, labels, "
    "logos, watermark or signature. Vertical 9:16."
)

REFERENCE_SET = {
    "moss_canonical": (
        "Redraw this exact character as the canonical reference: full body, "
        "three-quarter view, centered, neutral standing pose, goggles up on the "
        "forehead, on a solid flat navy background."
    ),
    "moss_front": "Moss facing the viewer straight on, full body, neutral pose, goggles up.",
    "moss_side": "Moss in a clean side profile facing right, full body, goggles up.",
    "moss_amazed": (
        "Expression sheet pose: Moss amazed, goggles pulled down over the eyes, "
        "mouth a small open 'o', two tiny amber sparkle pixels beside the head."
    ),
    "moss_thinking": (
        "Expression sheet pose: Moss thinking, goggles up, eyes glancing up and to "
        "the side, one front claw raised to its chin."
    ),
    "moss_delighted": (
        "Expression sheet pose: Moss delighted, eyes closed in happy upturned arcs, "
        "small smile, mid-hop with both front claws raised."
    ),
}

STORY = {
    "1_hook": (
        "Scene: a thick sliced meteorite slab whose cut face is speckled with pale "
        "round grains floats in the middle of the frame (35-60% height). Moss stands "
        "on top of it wearing exactly ONE pair of goggles, pulled down over its eyes, "
        "leaning in amazed. Neon magenta and amber "
        "magnetic field lines arc out of the rock and loop high over Moss into the "
        "upper part of the frame."
    ),
    "2_antarctica": (
        "Scene: night over Antarctica. A small dark meteorite rests on blue-white "
        "pixel ice in the middle of the frame (40-60% height), with a low ridge of "
        "ice behind it. Moss peeks out from behind the meteorite on the right side, "
        "goggles up, curious. Below 62% the ice turns to plain dark shadow."
    ),
    "3_disk": (
        "Scene: the early solar system. A young glowing Sun sits at the centre of a "
        "flat, smooth protoplanetary disk: a thin ring of gas and dust seen at a gentle "
        "angle. It is not a galaxy: no spiral arms and no planets yet. Neon magenta "
        "magnetic field lines thread vertically through the disk, and amber dust "
        "streams inward along them toward the Sun. The disk sits at 30-55% height. "
        "Moss floats small in the upper-left corner, goggles up, watching calmly."
    ),
    "4_compare": (
        "Scene: comparison. On the left, a small pixel Earth with its magnetic field "
        "drawn as two small amber dipole loops above and below it, like a bar magnet's "
        "field; Earth has no planetary ring. On the right, a glowing young Sun with "
        "huge nested neon magenta dipole field loops several times bigger. Earth and "
        "Sun both sit between 35% and 55% of the frame height, side by side. Moss "
        "hovers at the top centre above them, delighted, both front claws raised."
    ),
}


def generate(prompt: str, path: Path, references: list[Image.Image]) -> Image.Image:
    if path.exists():
        print("skip (exists)", path.name)
        return Image.open(path).convert("RGB")
    arguments = {"prompt": prompt, "aspect_ratio": "9:16", "num_images": 1, "output_format": "png"}
    model = TEXT_MODEL
    if references:
        arguments["image_urls"] = [fal_client.upload_image(ref, format="png") for ref in references]
        model = EDIT_MODEL
    for attempt in range(2):
        try:
            result = fal_client.run(model, arguments=arguments, timeout=180)
            response = requests.get(result["images"][0]["url"], timeout=60)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            image.save(path)
            print("saved", path.relative_to(HERE), image.size)
            return image
        except Exception as exc:  # noqa: BLE001 - surface and retry once
            print(f"attempt {attempt + 1} failed for {path.name}: {exc}", file=sys.stderr)
            time.sleep(3)
    raise RuntimeError(f"could not generate {path.name}")


def main():
    seed = Image.open(ROUND1_REFERENCE).convert("RGB")
    canonical = generate(
        f"{REFERENCE_SET['moss_canonical']}\n\n{MOSS}\n\n{STYLE}\n\n{AVOID}",
        HERE / "moss" / "moss_canonical.png",
        [seed],
    )
    for name, pose in REFERENCE_SET.items():
        if name == "moss_canonical":
            continue
        generate(f"{pose}\n\n{MOSS}\n\n{STYLE}\n\n{AVOID}", HERE / "moss" / f"{name}.png", [canonical])
    for name, scene in STORY.items():
        generate(
            f"{scene}\n\n{MOSS}\n\n{STYLE}\n\n{CAPTION_BAND}\n\n{AVOID}",
            HERE / "story" / f"{name}.png",
            [canonical],
        )


if __name__ == "__main__":
    main()

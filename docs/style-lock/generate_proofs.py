"""Generate style-lock proof frames: one mascot reference + two story beats per route.

Run from the repo root: python docs/style-lock/generate_proofs.py
Uses Nano Banana via FAL (the Gemini key is free-tier, which allows no image
generation). ~9 images, about $0.039 each.
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
OUT = Path(__file__).resolve().parent / "proofs"

MOSS = (
    "Character anchor: Moss, an original cartoon mascot based on a tardigrade "
    "(a 'moss piglet'). Plump soft bean-shaped barrel body with three gentle "
    "segment creases, soft mint-sage body with a slightly lighter belly, eight "
    "short stubby legs (four visible per side) each ending in two tiny claws, a "
    "short round snout with a small round 'o' mouth, two small glossy black bead "
    "eyes, and brass explorer goggles with amber lenses worn on the forehead. "
    "Curious, calm, quietly amazed. Non-realistic and friendly; no hands, no "
    "fingers, no clothing besides the goggles."
)
AVOID = (
    "Avoid: realistic microscope-photo tardigrade texture, teeth or scary mouth, "
    "more than eight legs, arms or human hands, extra accessories, any text, "
    "letters, numbers, labels, logos, watermark or signature."
)

ROUTES = {
    "A_ink_field_notes": (
        "Style: flat 2D hand-inked editorial science illustration on warm "
        "off-white paper (#F4F0E6). Confident cobalt-blue ink contours and flat "
        "blue fills, one warm yellow highlight on the focal object, a small red "
        "accent. Generous negative space, one clear focal subject, no perspective "
        "depth, subtle paper grain. Moss is drawn in the same ink language with "
        "flat mint fill."
    ),
    "B_pixel_night_lab": (
        "Style: chunky 16-bit pixel art with a crisp visible pixel grid and no "
        "anti-aliasing blur. Deep navy-to-black background (#0B1026), saturated "
        "neon accents of magenta (#FF3DA8) and amber (#FFB000), bright mint Moss "
        "as the brightest shape in frame. High contrast, dense but readable, small "
        "twinkling pixel stars, playful retro game-console energy."
    ),
    "C_clay_diorama": (
        "Style: handmade claymation miniature diorama photographed with a macro "
        "lens. Moss is a soft matte modelling-clay figure with visible thumbprint "
        "texture. Props are painted clay, felt and paper. Warm practical lighting "
        "from one side, shallow depth of field with creamy bokeh, tilt-shift "
        "miniature feel, rich warm midtones, tactile and premium."
    ),
}

REFERENCE_SCENE = (
    "Scene: canonical reference portrait of Moss alone, three-quarter view, full "
    "body visible and centered, neutral standing pose, plain uncluttered "
    "background in the style's base colour, even lighting so every anchor is "
    "clearly visible. Vertical 9:16."
)
STORY_SCENES = {
    "hook": (
        "Scene: Moss stands on top of a thick sliced meteorite slab whose cut face "
        "is speckled with pale round grains. Glowing magnetic field lines arc out "
        "of the rock and loop over Moss. Moss has pulled the goggles down over its "
        "eyes and leans in, amazed. Low camera angle, meteorite slab fills the "
        "lower half, field lines fill the upper half. Vertical 9:16. Keep the "
        "lower-middle band of the frame simple so burned-in captions stay readable."
    ),
    "explain": (
        "Scene: the young Sun as a glowing ball at the centre of a swirling flat "
        "disk of gas and dust, seen at a gentle angle. Curved magnetic field lines "
        "thread through the disk and pull dust inward toward the Sun. Moss floats "
        "small in empty space in the lower-left foreground, goggles up, watching "
        "calmly. Wide shot, strong depth from Moss to the disk. Vertical 9:16. "
        "Keep the lower-middle band of the frame simple for captions."
    ),
}


def generate(prompt: str, path: Path, reference: Image.Image | None = None) -> Image.Image:
    if path.exists():
        print("skip (exists)", path.name)
        return Image.open(path)
    arguments = {"prompt": prompt, "aspect_ratio": "9:16", "num_images": 1, "output_format": "png"}
    model = TEXT_MODEL
    if reference is not None:
        arguments["image_urls"] = [fal_client.upload_image(reference, format="png")]
        model = EDIT_MODEL
    for attempt in range(2):
        try:
            result = fal_client.run(model, arguments=arguments, timeout=180)
            url = result["images"][0]["url"]
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            image = Image.open(BytesIO(response.content)).convert("RGB")
            image.save(path)
            print("saved", path.name, image.size)
            return image
        except Exception as exc:  # noqa: BLE001 - surface and retry once
            print(f"attempt {attempt + 1} failed for {path.name}: {exc}", file=sys.stderr)
            time.sleep(3)
    raise RuntimeError(f"could not generate {path.name}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for route, style in ROUTES.items():
        reference = generate(
            f"{MOSS}\n\n{REFERENCE_SCENE}\n\n{style}\n\n{AVOID}",
            OUT / f"{route}_0_reference.png",
        )
        for beat, scene in STORY_SCENES.items():
            generate(
                "The attached image is the approved reference for Moss. Keep "
                "Moss's identity, proportions, colours and goggles exactly the "
                "same as in the reference, but compose a completely new scene.\n\n"
                f"{MOSS}\n\n{scene}\n\n{style}\n\n{AVOID}",
                OUT / f"{route}_{'1' if beat == 'hook' else '2'}_{beat}.png",
                reference=reference,
            )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the retention renderer against synthetic, non-private science copy."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from PIL import Image, ImageChops, ImageStat


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from video_generator import (  # noqa: E402
    FAL_HOOK_IMAGE_COST_USD,
    FAL_HOOK_IMAGE_MODEL,
    FAL_IMAGE_COST_USD,
    FAL_IMAGE_MODEL,
    generate_video,
)


TITLE = "Hubble tracks changes inside Jupiter’s Great Red Spot over decades"
COVER_LINE = "JUPITER’S STORM IS SHRINKING"
SEARCH_CAPTION = "Why Jupiter’s Great Red Spot is shrinking."
CTA_QUESTION = "Would you miss Jupiter’s giant red eye?"
HASHTAGS = ["#Jupiter", "#GreatRedSpot", "#SpaceScience"]
SCENES = [
    {
        "speech": (
            "Jupiter’s giant storm is slowly shrinking, and nobody knows "
            "exactly when it will disappear."
        ),
        "visual": (
            "Jupiter filling the frame with the Great Red Spot rotating clearly"
        ),
        "emotion": "curious",
    },
    {
        "speech": (
            "The Great Red Spot has raged for at least two centuries, spinning "
            "counterclockwise beneath bands of ammonia clouds."
        ),
        "visual": (
            "layered ammonia cloud bands spiraling around Jupiter’s red storm"
        ),
        "emotion": "curious",
    },
    {
        "speech": (
            "At its widest, the storm once spanned more than twice Earth’s "
            "diameter, large enough to swallow our planet whole."
        ),
        "visual": (
            "Earth placed beside the Great Red Spot to show their enormous "
            "scale difference"
        ),
        "emotion": "shocking",
    },
    {
        "speech": (
            "But Hubble measurements show its long axis has been contracting "
            "for decades, while its winds remain violently fast."
        ),
        "visual": (
            "Hubble telescope observations comparing the red storm across "
            "several decades"
        ),
        "emotion": "urgent",
    },
    {
        "speech": "The strange turn is that shrinking may not mean weakening.",
        "visual": (
            "the red vortex tightening while bright cloud bands accelerate "
            "around it"
        ),
        "emotion": "curious",
    },
    {
        "speech": (
            "As the storm narrows, some observations suggest its winds can "
            "accelerate, like a skater pulling in their arms."
        ),
        "visual": (
            "fast cloud currents circling a narrowing planetary vortex beside "
            "a spinning skater silhouette"
        ),
        "emotion": "shocking",
    },
    {
        "speech": (
            "Smaller storms may also feed it by merging with the red vortex "
            "and transferring fresh energy."
        ),
        "visual": (
            "several small white Jovian storms merging into the Great Red Spot"
        ),
        "emotion": "curious",
    },
    {
        "speech": (
            "Scientists compare yearly images to track changes in size, color, "
            "height, and wind speed."
        ),
        "visual": (
            "planetary scientists comparing aligned yearly Jupiter images in "
            "an observatory"
        ),
        "emotion": "curious",
    },
    {
        "speech": (
            "That record could reveal whether the storm is dying, stabilizing, "
            "or transforming into something new."
        ),
        "visual": (
            "three possible futures of Jupiter’s red storm shown as distinct "
            "planetary views"
        ),
        "emotion": "curious",
    },
    {
        "speech": CTA_QUESTION,
        "visual": (
            "Jupiter without the Great Red Spot beside its familiar "
            "present-day appearance"
        ),
        "emotion": "curious",
    },
]


def _extract_frame(video_path: Path, second: int, output_path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(second),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        check=True,
        timeout=60,
    )


def _frame_difference(left: Path, right: Path) -> float:
    with Image.open(left) as left_image, Image.open(right) as right_image:
        left_small = left_image.convert("RGB").resize((90, 160))
        right_small = right_image.convert("RGB").resize((90, 160))
        difference = ImageChops.difference(left_small, right_small)
        return sum(ImageStat.Stat(difference).mean) / 3


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )
    script = " ".join(scene["speech"] for scene in SCENES)
    caption = "\n\n".join(
        [SEARCH_CAPTION, CTA_QUESTION, " ".join(HASHTAGS)]
    )
    print(
        "ACCEPTANCE_SUMMARY="
        + json.dumps(
            {
                "old_title": TITLE,
                "cover_line": COVER_LINE,
                "word_count": len(script.split()),
                "series_lane": "space",
                "caption": caption,
                "scene_count": len(SCENES),
                "body_model": FAL_IMAGE_MODEL,
                "hook_model": FAL_HOOK_IMAGE_MODEL,
                "body_unit_cost": FAL_IMAGE_COST_USD,
                "hook_unit_cost": FAL_HOOK_IMAGE_COST_USD,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    output = Path(
        generate_video(
            article_id=999001,
            title=TITLE,
            script=script,
            image_source="mixed",
            captions=True,
            scenes=SCENES,
            style_key="cinematic",
            emotion="curious",
            use_video_hook=False,
            voice_tone="controlled",
            cover_line=COVER_LINE,
            series_lane="space",
            hero_image=None,
        )
    ).resolve()

    with TemporaryDirectory(prefix="clipper-retention-frames-") as temp_dir:
        frames = []
        for second in range(5):
            frame = Path(temp_dir) / f"frame-{second}.jpg"
            _extract_frame(output, second, frame)
            frames.append(frame)
        differences = [
            round(_frame_difference(frames[index], frames[index + 1]), 3)
            for index in range(len(frames) - 1)
        ]
        meaningful_changes = sum(value >= 8.0 for value in differences)

    print(
        "ACCEPTANCE_RENDER="
        + json.dumps(
            {
                "video_path": str(output),
                "frame_seconds": [0, 1, 2, 3, 4],
                "adjacent_frame_differences": differences,
                "meaningful_visual_changes": meaningful_changes,
            }
        ),
        flush=True,
    )
    if meaningful_changes < 2:
        raise RuntimeError("first five seconds changed fewer than two times")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

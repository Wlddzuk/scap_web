"""Moss, animated in code: walks in and out, idles, blinks and reacts to narration.

Moss is never painted into the generated scenes. He is a sprite layer drawn
over them, which keeps him identical in every frame, keeps him out of the
caption band, and costs nothing per video. Poses come from
assets/moss/sprites/ (build with scripts/build_moss_sprites.py).

Motion is stepped at SPRITE_FPS and snapped to a coarse pixel grid so he moves
like a game sprite rather than a smooth tween, matching the pixel-art world.
"""

import functools
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from moviepy.editor import VideoClip
from PIL import Image

import video_generator as vg

SPRITE_DIR = Path(__file__).resolve().parent / "assets" / "moss" / "sprites"
POSES = (
    "canonical", "thinking", "side", "amazed", "point",
    "wave", "delighted", "blink", "walk_a", "walk_b",
)
# Sprites whose art faces screen-right; they are mirrored when Moss stands on
# the right so he always faces into the frame.
FACES_RIGHT = {"side", "point", "walk_a", "walk_b"}

MOSS_HEIGHT = 240           # px, about 12% of the frame
FEET_Y = 1180               # just above the caption band (62% of 1920)
EDGE_MARGIN = 56
SPRITE_FPS = 12
GRID = 4
WALK_SECONDS = 0.6
WALK_FRAME_SECONDS = 0.15
HOP_SECONDS = 0.3
HOP_HEIGHT = 34
BLINK_EVERY = 3.4
BLINK_SECONDS = 0.13
POINT_SECONDS = 0.9

_MAGNITUDE_WORDS = {
    "times", "stronger", "faster", "bigger", "larger", "million", "millions",
    "billion", "billions", "trillion", "percent", "%", "record", "first",
}
_DELIGHT_WORDS = {"success", "successful", "works", "worked", "cured", "remission", "breakthrough"}


@dataclass(frozen=True)
class Appearance:
    start: float
    end: float
    side: str            # "left" or "right"
    enter: bool = True
    leave: bool = True


@dataclass(frozen=True)
class Reaction:
    start: float
    end: float
    pose: str
    priority: int


def plan_appearances(scene_spans: list[tuple[float, float]], duration: float) -> list[Appearance]:
    """Moss hosts the hook, then every other scene, and always the last one."""
    if not scene_spans:
        return [Appearance(0.0, duration, "left", enter=True, leave=False)]
    chosen = sorted({0, len(scene_spans) - 1, *range(0, len(scene_spans), 2)})
    spans: list[list[float]] = []
    for index in chosen:
        start, end = scene_spans[index]
        if spans and abs(spans[-1][1] - start) < 0.05:
            spans[-1][1] = end              # consecutive scenes: stay on screen
        else:
            spans.append([start, end])
    appearances = []
    for number, (start, end) in enumerate(spans):
        is_last = end >= duration - 0.05
        appearances.append(Appearance(
            start=start,
            end=min(end, duration),
            side="left" if number % 2 == 0 else "right",
            enter=True,
            leave=not is_last,
        ))
    return appearances


def plan_reactions(timed_words: list, hook_len: float) -> list[Reaction]:
    """Turn narration word timings into pose changes."""
    reactions = [Reaction(0.2, min(1.8, max(0.6, hook_len)), "amazed", 3)]
    sentence_start = None
    for word in timed_words or []:
        text = str(word.get("text", ""))
        key = vg._caption_token_key(text)
        start, end = float(word["start"]), float(word["end"])
        if sentence_start is None:
            sentence_start = start
        if key == "follow":
            reactions.append(Reaction(start, start + 1.6, "wave", 4))
        elif vg._caption_token_is_number(text) or key in _MAGNITUDE_WORDS:
            reactions.append(Reaction(start, start + 0.9, "amazed", 3))
        elif key in _DELIGHT_WORDS:
            reactions.append(Reaction(start, start + 1.0, "delighted", 2))
        if re.search(r"[.!?]$", text):
            if text.endswith("?"):
                reactions.append(Reaction(sentence_start, end, "thinking", 1))
            sentence_start = None
    return reactions


@functools.lru_cache(maxsize=1)
def _load_sprites() -> dict[str, Image.Image]:
    canonical = Image.open(SPRITE_DIR / "canonical.png").convert("RGBA")
    scale = MOSS_HEIGHT / canonical.height
    sprites = {}
    for pose in POSES:
        path = SPRITE_DIR / f"{pose}.png"
        if not path.exists():
            continue
        image = Image.open(path).convert("RGBA")
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        sprites[pose] = image.resize(size, Image.Resampling.NEAREST)
    return sprites


@functools.lru_cache(maxsize=64)
def _sprite(pose: str, mirrored: bool) -> Image.Image:
    sprites = _load_sprites()
    image = sprites.get(pose) or sprites["canonical"]
    return image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mirrored else image


def _hop(elapsed: float) -> float:
    if 0.0 <= elapsed < HOP_SECONDS:
        return -math.sin(elapsed / HOP_SECONDS * math.pi) * HOP_HEIGHT
    return 0.0


def moss_state(t: float, appearances: list[Appearance], reactions: list[Reaction]):
    """Return (pose, x, y_offset, mirrored) at time t, or None when off screen."""
    t = math.floor(t * SPRITE_FPS) / SPRITE_FPS
    appearance = next((a for a in appearances if a.start <= t < a.end), None)
    if appearance is None:
        return None
    width = _load_sprites()["canonical"].width
    on_right = appearance.side == "right"
    home_x = vg.VIDEO_WIDTH - EDGE_MARGIN - width if on_right else EDGE_MARGIN
    offscreen_x = vg.VIDEO_WIDTH + 8 if on_right else -width - 8
    walk_frame = "walk_a" if int(t / WALK_FRAME_SECONDS) % 2 == 0 else "walk_b"
    bob = -abs(math.sin(t * math.pi * 1.4)) * 6

    if appearance.enter and t < appearance.start + WALK_SECONDS:
        progress = (t - appearance.start) / WALK_SECONDS
        x = offscreen_x + (home_x - offscreen_x) * progress
        # Walking in toward the centre: from the right he faces left.
        return walk_frame, x, bob * 2, on_right
    if appearance.leave and t >= appearance.end - WALK_SECONDS:
        progress = (t - (appearance.end - WALK_SECONDS)) / WALK_SECONDS
        x = home_x + (offscreen_x - home_x) * progress
        return walk_frame, x, bob * 2, not on_right

    active = [r for r in reactions if r.start <= t < r.end]
    if active:
        reaction = max(active, key=lambda r: (r.priority, r.start))
        y = _hop(t - reaction.start) + (0 if reaction.pose == "thinking" else bob)
        return reaction.pose, home_x, y, on_right
    if appearance.enter and t < appearance.start + WALK_SECONDS + POINT_SECONDS:
        return "point", home_x, bob, on_right
    if (t % BLINK_EVERY) < BLINK_SECONDS:
        return "blink", home_x, bob, False
    return "canonical", home_x, bob, False


def create_moss_overlay(
    duration: float,
    scene_spans: list[tuple[float, float]],
    timed_words: list,
    hook_len: float,
):
    """A small RGBA sprite clip positioned per frame; None if sprites are missing."""
    if not (SPRITE_DIR / "canonical.png").exists():
        return None
    appearances = plan_appearances(scene_spans, duration)
    reactions = plan_reactions(timed_words, hook_len)
    sprites = _load_sprites()
    canvas_w = max(image.width for image in sprites.values()) + GRID
    canvas_h = max(image.height for image in sprites.values()) + GRID

    @functools.lru_cache(maxsize=256)
    def render(step: int):
        state = moss_state(step / SPRITE_FPS, appearances, reactions)
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        if state is None:
            return canvas, (0, 0)
        pose, x, y, mirrored = state
        sprite = _sprite(pose, mirrored)
        canvas.alpha_composite(sprite, ((canvas_w - sprite.width) // 2, canvas_h - sprite.height))
        left = int(round(x / GRID) * GRID) - (canvas_w - sprites["canonical"].width) // 2
        top = int(round((FEET_Y - canvas_h + y) / GRID) * GRID)
        return canvas, (left, top)

    def step_at(t):
        return int(math.floor(t * SPRITE_FPS))

    def make_frame(t):
        return np.asarray(render(step_at(t))[0].convert("RGB"))

    def make_mask(t):
        return np.asarray(render(step_at(t))[0].getchannel("A"), dtype=np.float32) / 255.0

    clip = VideoClip(make_frame, duration=duration)
    clip = clip.set_mask(VideoClip(make_mask, ismask=True, duration=duration))
    return clip.set_position(lambda t: render(step_at(t))[1])

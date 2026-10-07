"""Put scene images on a true pixel grid, and move the camera in whole pixels.

The scene model draws "pixel art" with soft edges, off-grid pixels and any colour,
and a smooth LANCZOS zoom smeared it further. Here each scene is redrawn at a low
internal resolution (216x384), snapped to a limited palette with an ordered Bayer
dither, and scaled up nearest-neighbour by a whole number, so every pixel is an
equal square, like Moss. Pans move one low-res pixel per 12 fps step (Moss's
rhythm); a push is a stepped punch-in to a 6 px window instead of a smooth zoom.
The method follows the pixel style of github.com/cth9191/animate (MIT).
"""

import numpy as np
from PIL import Image

W, H = 1080, 1920
BASE_PX = 5                    # base grid: 216x384, 5 screen px per pixel
CROP_PX = 6                    # framing window: 180x320 of the base, 6 px per pixel
STEP_FPS = 12                  # Moss moves on the same 12 fps step
COLORS = 24                    # adaptive colours, picked from the subject
DITHER_STRENGTH = 10.0         # stronger dithers the flat navy into a checkerboard
LOCKED = ("#0B1026", "#FF3DA8", "#FFB000")   # STYLE.md: ground navy, magenta, amber
_SUBJECT_DISTANCE = 45         # RGB distance from navy that counts as subject

_BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16.0


def _rgb(hexcode: str) -> list:
    return [int(hexcode[i:i + 2], 16) for i in (1, 3, 5)]


def _cover(image: Image.Image, w: int, h: int) -> Image.Image:
    """Centre-crop to the w:h aspect ratio, then area-average down to w x h."""
    src = image.convert("RGB")
    sw, sh = src.size
    if sw / sh > w / h:
        nw = round(sh * w / h)
        src = src.crop(((sw - nw) // 2, 0, (sw - nw) // 2 + nw, sh))
    else:
        nh = round(sw * h / w)
        src = src.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
    return src.resize((w, h), Image.Resampling.BOX)


def _palette(arr: np.ndarray) -> np.ndarray:
    """The subject's own colours, the locked style colours, and the image's navy ground."""
    navy = np.array(_rgb(LOCKED[0]), np.float32)
    far = np.sqrt(((arr - navy) ** 2).sum(-1)) > _SUBJECT_DISTANCE
    subject = arr[far] if far.sum() >= 64 else arr.reshape(-1, 3)
    ground = np.median(arr[~far], axis=0) if (~far).sum() >= 64 else navy
    count = COLORS - len(LOCKED)
    adaptive = Image.fromarray(subject.astype(np.uint8)[None]).quantize(count, method=Image.Quantize.MEDIANCUT)
    colours = np.array(adaptive.getpalette()[: 3 * count], np.float32).reshape(-1, 3)
    # ground and two darker steps of it for the graded caption bands
    return np.vstack([colours, [_rgb(c) for c in LOCKED], [ground, ground * 0.75, ground * 0.55]])


def gridify(image: Image.Image, grade_mask=None, px: int = BASE_PX) -> np.ndarray:
    """The scene at its low internal resolution, palette-snapped: an (h, w, 3) uint8 array.

    ``grade_mask(w, h)`` returns an "L" image darkening the headline and caption bands;
    it is applied before the snap so the darkening dithers like everything else.
    """
    w, h = W // px, H // px
    arr = np.asarray(_cover(image, w, h), np.float32)
    arr = np.clip(arr * 1.06 - 0.06 * 127.5, 0, 255)          # the old +6% contrast
    if grade_mask is not None:
        alpha = (np.asarray(grade_mask(w, h), np.float32) / 255.0)[..., None]
        arr = arr * (1 - alpha) + np.array([7, 12, 25], np.float32) * alpha
    pal = _palette(arr)
    threshold = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w] - 0.5
    nudged = arr + threshold[..., None] * DITHER_STRENGTH
    nearest = ((nudged[:, :, None, :] - pal[None, None]) ** 2).sum(-1).argmin(-1)
    return pal[nearest].astype(np.uint8)


def upscale(low: np.ndarray, px: int) -> np.ndarray:
    return np.repeat(np.repeat(low, px, axis=0), px, axis=1)


def _window(low: np.ndarray, anchor) -> tuple:
    """Top-left of the CROP_PX window placed at a framing anchor (0..1, 0..1)."""
    h, w = low.shape[:2]
    return int(round((w - W // CROP_PX) * anchor[0])), int(round((h - H // CROP_PX) * anchor[1]))


def _crop(low: np.ndarray, x: int, y: int) -> np.ndarray:
    return upscale(low[y:y + H // CROP_PX, x:x + W // CROP_PX], CROP_PX)


def frame_at(low: np.ndarray, t: float, duration: float, motion: str, anchor) -> np.ndarray:
    """One 1080x1920 frame of a shot. Every pixel stays a whole square at every step."""
    h, w = low.shape[:2]
    steps = max(1, int(duration * STEP_FPS))
    step = min(steps, int(max(0.0, t) * STEP_FPS))
    if motion in ("push", "pull"):
        # wide on the base grid, punched in to the window for the rest of the shot
        # (a pull plays it the other way round)
        wide = step < steps // 3 if motion == "push" else step >= (2 * steps) // 3
        return upscale(low, BASE_PX) if wide else _crop(low, *_window(low, anchor))
    # pans slide the window one low-res pixel per step across the slack
    slack = w - W // CROP_PX
    travel = min(slack, steps)
    start = (slack - travel) // 2
    moved = (step * travel) // steps
    x = start + moved if motion == "pan-right" else start + travel - moved
    return _crop(low, x, _window(low, anchor)[1])


def gridded_shot(low: np.ndarray, anchor) -> Image.Image:
    """The shot's still (its framing window), carrying the grid for create_clip to move."""
    image = Image.fromarray(_crop(low, *_window(low, anchor)))
    image.pixel_grid = (low, anchor)
    return image


def grid_of(image):
    """(low-res picture, framing anchor) for a gridded shot, or None for any other image."""
    return getattr(image, "pixel_grid", None)

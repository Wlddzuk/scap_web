"""Look at each generated scene image before the video is stitched together.

Z-Image Turbo has no negative prompt and draws every noun it reads, so a scene can
come back with lettering, a mascot look-alike or an object nobody asked for. A
cheap vision model on OpenRouter (about $0.0001 per image) answers a few yes/no questions per image;
pixel_scenes regenerates a failed scene once and keeps whichever image has fewer
issues. The checker never blocks a render: when it is unavailable, every image passes.
"""

import base64
import io
import json
import logging
import os

import requests
from dataclasses import dataclass, field

from dotenv import load_dotenv
from PIL import Image

load_dotenv()
logger = logging.getLogger(__name__)

SCENE_CHECK_MODEL = os.getenv("SCENE_CHECK_MODEL", "").strip() or "google/gemini-2.5-flash-lite"
CHECK_IMAGE_WIDTH = 512          # plenty to spot lettering; keeps the request small
CHECK_TIMEOUT_SECONDS = 30

_PROMPT = """You are checking one scene image for a pixel-art science video before it is published.
The scene was supposed to show: "{visual}"
The house style ALWAYS adds these, so they are expected and never count as problems: a deep navy
background, a visible pixel grid, neon magenta and amber energy lines, sparks, glowing dots and
particles, glow rings around objects, small stars, and any colour choices.

Answer with JSON only:
{{
  "text": true if ANY letters, words, numbers, labels or fake writing appear anywhere (signage, screens, objects),
  "framed": true if the scene is drawn as a picture INSIDE the image (a card, poster, panel, frame or border) with a white, grey or light margin around it, instead of filling the whole image edge to edge,
  "character": true if a cartoon creature, mascot or character with a face appears that the scene did not ask for,
  "unrequested": [short names of PROMINENT objects that the scene did not ask for and that change its meaning, e.g. "ringed planet", "bar chart"; ignore small sparkles, stars, energy lines and background details],
  "matches": true if the main subject of the image is what the scene asked for,
  "note": "one short sentence"
}}"""


@dataclass
class SceneVerdict:
    text: bool = False
    framed: bool = False
    character: bool = False
    unrequested: list = field(default_factory=list)
    matches: bool = True
    note: str = ""
    checked: bool = False      # False when the checker was unavailable (the image passes)

    @property
    def issues(self) -> list:
        """Blocking problems. Unrequested objects are only advisory (the style's own
        energy lines and glows were flagged too often to regenerate on them)."""
        found = []
        if self.text:
            found.append("text")
        if self.framed:
            found.append("framed")
        if self.character:
            found.append("character")
        if not self.matches:
            found.append("off-brief")
        return found

    @property
    def score(self) -> int:
        """Lower is better: blocking issues weigh more than stray objects."""
        return 10 * len(self.issues) + len(self.unrequested)

    @property
    def passed(self) -> bool:
        return not self.issues


def enabled() -> bool:
    flag = os.getenv("SCENE_CHECK", "on").strip().lower()
    return flag not in {"0", "off", "false", "no"} and bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def parse_verdict(raw: str) -> SceneVerdict:
    data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```"))
    unrequested = data.get("unrequested") or []
    if not isinstance(unrequested, list):
        unrequested = [str(unrequested)]
    return SceneVerdict(
        text=bool(data.get("text")),
        framed=bool(data.get("framed")),
        character=bool(data.get("character")),
        unrequested=[str(name).strip() for name in unrequested if str(name).strip()][:4],
        matches=data.get("matches") is not False,
        note=str(data.get("note") or "")[:200],
        checked=True,
    )


def check_scene_image(image: Image.Image, visual: str) -> SceneVerdict:
    """Ask the vision model what is in the image; pass on any failure."""
    if not enabled():
        return SceneVerdict()
    try:
        small = image.convert("RGB")
        small = small.resize(
            (CHECK_IMAGE_WIDTH, round(small.height * CHECK_IMAGE_WIDTH / small.width)),
            Image.Resampling.LANCZOS,
        )
        buffer = io.BytesIO()
        small.save(buffer, format="JPEG", quality=90)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
                "HTTP-Referer": "http://localhost:5050",
                "X-Title": "Clipper",
            },
            json={
                "model": SCENE_CHECK_MODEL,
                "temperature": 0,
                "max_tokens": 300,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": _PROMPT.format(visual=visual.replace('"', "'")[:400])},
                ]}],
            },
            timeout=CHECK_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        message = (response.json().get("choices") or [{}])[0].get("message") or {}
        return parse_verdict(message.get("content") or "")
    except Exception as exc:
        logger.info("[SceneCheck] Unavailable, passing image: %s", exc)
        return SceneVerdict()

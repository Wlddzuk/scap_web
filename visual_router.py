"""Deterministic referent-based routing for Clipper's scene visuals."""

from __future__ import annotations


PHOTO = "photo"
SCHEMATIC = "schematic"
GRAPHIC = "graphic"


def route_scene(scene: dict) -> str:
    """Return ``photo`` | ``schematic`` | ``graphic`` for one scene."""
    scene = scene if isinstance(scene, dict) else {}
    referent = str(scene.get("referent") or "").strip().lower()
    query = str(scene.get("referent_query") or "").strip()
    precise_claim = scene.get("precise_claim") is True

    if referent == "abstract":
        return GRAPHIC
    if referent == "object" and query:
        return PHOTO
    if referent == "unphotographable" and precise_claim:
        return GRAPHIC
    if referent == "unphotographable" and not precise_claim:
        return SCHEMATIC
    return GRAPHIC

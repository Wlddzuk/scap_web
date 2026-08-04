#!/usr/bin/env python3
"""Mechanically check a visual plan against the countable rules in SKILL.md.

Stdlib only. Reads one JSON file, writes a report to stdout, exits 1 on failure.
It makes no network calls and modifies nothing.

    python check_plan.py plan.json

Expected shape (see sample_plan.json):

    {
      "narration_seconds": 48.0,
      "beats": [
        {
          "id": 1,
          "idea": "...",
          "referent": "object|unphotographable|abstract",
          "source": "real|ai|graphic",
          "image": "A1",
          "query": "...",             # required when source == "real"
          "ai_justification": "...",  # required when source == "ai"
          "shots": [
            {"start": 0.0, "end": 2.3, "motion": "push", "note": "..."}
          ]
        }
      ]
    }

Judgement failures (broken hook promise, scale whiplash, off-world imagery)
are NOT checkable here. Use evaluation/strong-vs-weak.md for those.
"""

import json
import sys

MAX_SHOT = 2.5          # mirrors MAX_SHOT_DURATION in video_generator.py
MIN_SHOT = 1.5          # below this a still reads as a flicker, not a cut
BEATS_MIN, BEATS_MAX = 5, 8
AI_RATIO_MAX = 0.40     # above this the plan is an AI slideshow
MOTIONS = {"push", "pull", "pan-left", "pan-right", "detail crop"}
REFERENTS = {"object", "unphotographable", "abstract"}
SOURCES = {"real", "ai", "graphic"}

# referent -> sources that are defensible for it.
# `abstract` may resolve to a real physical proxy (ladder rungs 1-2), a designed
# AI illustration of the comparison (rung 3), or a bare graphic (rung 4).
ROUTING = {
    "object": {"real"},
    "unphotographable": {"real", "ai"},
    "abstract": {"real", "ai", "graphic"},
}


def check(plan):
    """Return (failures, warnings, stats). Failures block; warnings inform."""
    fail, warn = [], []
    beats = plan.get("beats") or []

    if not beats:
        return ["plan has no beats"], [], {}

    # --- beat count -------------------------------------------------------
    if not BEATS_MIN <= len(beats) <= BEATS_MAX:
        msg = f"{len(beats)} beats, expected {BEATS_MIN}-{BEATS_MAX}"
        # Too many beats is the sentence-by-sentence failure; too few is thin.
        if len(beats) > BEATS_MAX:
            fail.append(f"{msg} - likely illustrating sentences, not beats")
        else:
            warn.append(msg)

    shots, images, ai_images = [], [], set()

    for beat in beats:
        bid = beat.get("id", "?")
        referent = beat.get("referent")
        source = beat.get("source")

        if referent not in REFERENTS:
            fail.append(f"beat {bid}: referent {referent!r} not in {sorted(REFERENTS)}")
        if source not in SOURCES:
            fail.append(f"beat {bid}: source {source!r} not in {sorted(SOURCES)}")

        # --- routing ------------------------------------------------------
        if referent in ROUTING and source in SOURCES:
            allowed = ROUTING[referent]
            if source not in allowed:
                fail.append(
                    f"beat {bid}: referent {referent!r} must route to "
                    f"{'/'.join(sorted(allowed))}, got {source!r}"
                )

        # --- physical proxy ladder ---------------------------------------
        # A bare graphic is rung 4: allowed, but it is the rung that loses
        # viewers, so it never passes silently.
        if referent == "abstract" and source == "graphic":
            warn.append(
                f"beat {bid}: abstract routed to a bare graphic (ladder rung 4) - "
                f"look for a real object at true scale first; a chart is a scroll-away"
            )

        # --- required justification --------------------------------------
        if source == "real" and not (beat.get("query") or "").strip():
            fail.append(f"beat {bid}: source 'real' needs a search query")
        if source == "ai":
            if not (beat.get("ai_justification") or "").strip():
                fail.append(
                    f"beat {bid}: every AI image needs ai_justification "
                    f"(why no real image exists)"
                )
            ai_images.add(beat.get("image") or f"_beat{bid}")

        if beat.get("image"):
            images.append(beat["image"])

        # --- shots --------------------------------------------------------
        beat_shots = beat.get("shots") or []
        if not beat_shots:
            fail.append(f"beat {bid}: no shots")
        for i, shot in enumerate(beat_shots):
            label = f"beat {bid} shot {i + 1}"
            try:
                dur = float(shot["end"]) - float(shot["start"])
            except (KeyError, TypeError, ValueError):
                fail.append(f"{label}: needs numeric start and end")
                continue
            shots.append(dur)
            if dur > MAX_SHOT + 1e-9:
                fail.append(
                    f"{label}: {dur:.2f}s exceeds the {MAX_SHOT}s cap "
                    f"- the renderer will split it arbitrarily"
                )
            elif dur < MIN_SHOT - 1e-9:
                warn.append(f"{label}: {dur:.2f}s is under {MIN_SHOT}s")
            motion = shot.get("motion")
            if motion not in MOTIONS:
                fail.append(f"{label}: motion {motion!r} not in {sorted(MOTIONS)}")

        # --- monotony -----------------------------------------------------
        motions = [s.get("motion") for s in beat_shots]
        if len(motions) >= 3 and len(set(motions)) == 1:
            warn.append(
                f"beat {bid}: {len(motions)} identical {motions[0]!r} moves - "
                f"reads as a slideshow"
            )

    # --- AI ratio ---------------------------------------------------------
    unique_images = set(images)
    ratio = len(ai_images) / len(unique_images) if unique_images else 0.0
    if ratio > AI_RATIO_MAX:
        fail.append(
            f"AI ratio {ratio:.0%} exceeds {AI_RATIO_MAX:.0%} - "
            f"re-route beats; most science subjects are photographable"
        )

    # --- anchor reuse -----------------------------------------------------
    if len(unique_images) == len(images) and len(images) > 2:
        warn.append("no anchor is reused - nothing ties the beats into one world")

    stats = {
        "beats": len(beats),
        "shots": len(shots),
        "longest_shot": round(max(shots), 2) if shots else 0.0,
        "avg_shot": round(sum(shots) / len(shots), 2) if shots else 0.0,
        "images": len(unique_images),
        "ai_images": len(ai_images),
        "ai_ratio": f"{ratio:.0%}",
    }
    return fail, warn, stats


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: check_plan.py <plan.json>")
        return 2
    try:
        with open(argv[1], encoding="utf-8") as handle:
            plan = json.load(handle)
    except FileNotFoundError:
        print(f"no such file: {argv[1]}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"invalid JSON: {exc}")
        return 2

    fail, warn, stats = check(plan)

    if stats:
        print("STATS")
        for key, value in stats.items():
            print(f"  {key:14} {value}")
        print()
    if warn:
        print("WARNINGS")
        for item in warn:
            print(f"  ~ {item}")
        print()
    if fail:
        print("FAILURES")
        for item in fail:
            print(f"  x {item}")
        print(f"\n{len(fail)} failure(s). Plan should not ship.")
        return 1

    print("All mechanical checks passed.")
    print("Judgement checks (hook promise, scale ladder, off-world imagery)")
    print("still need evaluation/strong-vs-weak.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

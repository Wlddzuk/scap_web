# Example — Pyramids: a hidden void found by muon tomography

**Why read this one:** the story's payoff is *inside a solid structure*. Nobody
has photographed it. This is the case where directors reach for AI and get it
wrong — the honest answer is that the scan figures are the real imagery.

## Narration (49s, 131 words)

> There is a room inside the Great Pyramid that no one has entered. In 2017,
> physicists pointed detectors at Khufu's pyramid, but they were not looking for
> doors. They were counting muons, particles created when cosmic rays hit the
> atmosphere. Muons pass through stone, and thicker stone absorbs more of them.
> So if you count muons arriving from every direction for months, empty space
> shows up as an excess. Above the Grand Gallery, they found one: a void at least
> thirty metres long. In 2023, an endoscope pushed through a joint in the
> stonework and returned the first images of a corridor nobody had seen in four
> and a half thousand years. Its purpose is still unknown. What do you think is
> inside?

## Hook

```
Line:    "There is a room inside the Great Pyramid that no one has entered."
Promise: the pyramid — and the sense of an interior
Frame 1: A1, Khufu's pyramid exterior, low angle, mass filling the frame.
         Delivers: the subject is this specific pyramid. The "inside" is
         promised by the framing weight, not by cutting away yet.
```

Note the discipline: the hook says *inside*, but frame 1 is the **outside**. You
earn the interior. Cutting to a speculative interior in frame 1 spends the
reveal before the viewer knows what they are looking at.

## Visual world

```
World:        one limestone mass, and the invisible particle rain that reads it
Anchors:      A1 Khufu exterior · A2 muon detector in situ · A3 muon density map ·
              A4 Grand Gallery interior · A5 endoscope corridor frame
Scale ladder: desert plateau → pyramid mass → interior gallery → stone joint →
              particle path
Palette:      limestone ochre, cobalt ink contours, one amber void highlight,
              off-white paper
Never show:   Assassin's Creed / game-engine pyramid interiors, glowing golden
              treasure chambers, aliens, pyramid-power mysticism, generic
              "Egypt travel" camel-at-sunset stock, a *different* pyramid
```

`Never show` is doing real work here. Pyramid stories attract exactly this
imagery, and one game-render frame destroys the credibility of the other 48
seconds.

## Beat plan

```
BEAT 1 · scenes 1-2 · 7.2s
  Idea:     this specific pyramid, and that it is solid
  Referent: object
  Source:   real search
  Image:    A1 — Great Pyramid of Khufu, Giza
  Query:    "Great Pyramid of Khufu Giza exterior" — Wikimedia Commons
            [fallback: "Khufu pyramid Giza plateau photograph"]
  Shots:
    1.a  0.0-2.4  push        low angle, mass fills frame
    1.b  2.4-4.6  detail crop hard cut — individual limestone blocks
    1.c  4.6-7.2  pull        full pyramid against sky

BEAT 2 · scenes 3-4 · 8.8s
  Idea:     the physicists and what they actually installed
  Referent: object
  Source:   real search
  Image:    A2 — ScanPyramids muon detector plates inside the pyramid
  Query:    "ScanPyramids muon detector Khufu installation" — ScanPyramids /
            Nagoya University press images
            [fallback: "nuclear emulsion muon detector plate"]
  Shots:
    2.a  0.0-2.3  push        detector plate in situ against stone
    2.b  2.3-4.5  detail crop hard cut — the emulsion film surface
    2.c  4.5-6.7  pan-left    across the detector array
    2.d  6.7-8.8  pull        detector small against the chamber

BEAT 3 · scenes 5-7 · 11.4s
  Idea:     the mechanism — muons rain down, stone absorbs, voids leak through
  Referent: unphotographable
  Source:   AI  (a muon is genuinely unphotographable, and no single published
                 figure shows rain + absorption + excess in one frame)
  Image:    A-mech — cosmic ray shower over a pyramid cross-section
  AI prompt: "fine parallel particle tracks falling from the sky through the
             cross-section of a large stone pyramid, most tracks stopping inside
             the stone, a few passing through an empty gap, single centred
             subject, clean space upper-left"
             Why AI: muons cannot be photographed; the absorption contrast is the
             whole mechanism and needs one purpose-built frame.
  Shots:
    3.a  0.0-2.2  push        tracks entering from above
    3.b  2.2-4.3  detail crop hard cut — tracks stopping inside dense stone
    3.c  4.3-6.4  pan-right   across the cross-section
    3.d  6.4-8.5  detail crop hard cut — tracks surviving through the gap
    3.e  8.5-11.4 pull        whole cross-section, gap now legible
        → split: 8.5-10.0 pull / 10.0-11.4 pull (2.5s cap)

BEAT 4 · scenes 8-9 · 8.6s
  Idea:     the result — the void appears in the data
  Referent: unphotographable
  Source:   real figure  (the muon density map is published data)
  Image:    A3 — ScanPyramids muon density anomaly map
  Query:    "ScanPyramids muon tomography density map big void" — Nature 2017 figure
            [fallback: "muon tomography anomaly map pyramid"]
  Shots:
    4.a  0.0-2.4  push        the density field
    4.b  2.4-4.6  detail crop hard cut — the excess region itself
    4.c  4.6-6.6  push        tighten on the anomaly
    4.d  6.6-8.6  pull        anomaly located relative to the Grand Gallery

BEAT 5 · scenes 10-11 · 8.2s
  Idea:     where it sits — above a place people have actually stood
  Referent: object
  Source:   real search
  Image:    A4 — Grand Gallery interior
  Query:    "Grand Gallery Great Pyramid interior corbelled" — Wikimedia Commons
            [fallback: "Khufu pyramid grand gallery photograph"]
  Shots:
    5.a  0.0-2.3  push        up the corbelled gallery
    5.b  2.3-4.4  pan-right   across the corbel steps
    5.c  4.4-6.3  detail crop hard cut — the ceiling joint
    5.d  6.3-8.2  push        into the stonework above

BEAT 6 · scenes 12-14 · 10.8s
  Idea:     the endoscope image — the actual payoff — then the question
  Referent: object
  Source:   real search   (this is the money shot; it exists, use it)
  Image:    A5 — endoscope frame of the north-face corridor
  Query:    "ScanPyramids endoscope corridor north face 2023" — ScanPyramids /
            Egyptian Ministry of Tourism and Antiquities release
            [fallback: "Great Pyramid hidden corridor endoscope image"]
  Shots:
    6.a  0.0-2.2  push        HARD CUT — the raw corridor frame
    6.b  2.2-4.3  detail crop hard cut — the corbelled ceiling inside
    6.c  4.3-6.4  pan-right   along the corridor's length
    6.d  6.4-8.5  pull        corridor recedes into dark
    6.e  8.5-10.8 pull        RETURN to A1 exterior, widest frame, under the CTA
        → split: 8.5-10.0 / 10.0-10.8

CHECKS
  Shots: 27, longest 2.5s, avg 2.0s
  Images: 6 anchors (5 real, 1 AI) — AI ratio 17%
  Anchor reuse: A1 opens and closes
```

## What makes this plan work

- **The interior is earned, not spent.** Frame 1 is the exterior; the endoscope
  frame lands at beat 6, where the narration pays it off.
- **The AI image is the only unphotographable thing** — the muon shower — and the
  justification is explicit. The void itself is *not* AI-generated, because the
  density map and the endoscope frame are real and stronger.
- **The temptation this plan refuses:** rendering a glowing hidden chamber. The
  narration says its purpose is unknown. Inventing an interior would assert a fact
  the source does not support, which is the same failure as fabricating a quote.
- **A1 bookends**, so a story about an unseen interior still resolves on the
  familiar exterior.

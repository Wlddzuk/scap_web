# Example — Technology: a neuromorphic chip that computes like neurons

**Why read this one:** technology stories are half photographable object (the
chip, the wafer, the lab rig) and half **pure number** (power, speed, cost).
This is the case where `abstract` → graphic matters most, because the temptation
is to generate glowing-circuit wallpaper for every statistic.

## Narration (54s, 142 words)

> Your laptop burns about a hundred watts to do what your brain does on twenty.
> Intel's Loihi 2 chip is an attempt to close that gap by copying the wiring.
> A normal processor separates memory from computation, so every operation drags
> data back and forth across a bus. That movement is where most of the energy
> goes. Loihi puts memory and computation in the same place, in about a million
> artificial neurons that only fire when their input changes. Nothing happens
> when nothing changes. On some sensing and control tasks, that design has come
> in around a hundred times more energy efficient than a conventional processor
> running the same workload. It is not a general purpose chip and it will not run
> your browser. But it points at a different way to build one. What would you
> want it to run?

## Hook

```
Line:    "Your laptop burns about a hundred watts to do what your brain does on twenty."
Promise: a comparison — two things, unequal
Frame 1: comparison graphic, 100 W vs 20 W, the two figures at true relative
         weight. Delivers: the hook IS the comparison; a graphic is the honest
         frame 1 here, not a picture of a laptop.
```

This is the one legitimate case for opening on a graphic rather than an object:
the hook's subject genuinely *is* a numerical comparison. Do not generalise it.

## Visual world

```
World:        silicon that is arranged like tissue, and the energy that buys
Anchors:      A1 power comparison graphic · A2 Loihi 2 die · A3 von Neumann bus
              diagram · A4 spiking neuron array · A5 energy-ratio graphic
Scale ladder: wafer → package → die → routing mesh → single spiking node
Palette:      cobalt ink, silicon grey, one amber energy highlight, off-white paper
Never show:   glowing blue "AI brain" wallpaper, humanoid robots, matrix-code
              rain, generic circuit-board stock, a literal human brain wired to a
              CPU, server-room b-roll
```

`Never show` here is the entire difference between a science channel and a
content farm. Tech stories attract stock "AI" imagery more than any other lane.

## Beat plan

```
BEAT 1 · scenes 1-2 · 7.4s
  Idea:     the gap — 100 W against 20 W
  Referent: abstract
  Source:   graphic
  Image:    A1 — two bars at true relative scale
  Payload:  "laptop 100 W · brain 20 W"
  Shots:
    1.a  0.0-2.3  push        the 100 W bar alone, dominant
    1.b  2.3-4.5  pull        HARD CUT — the 20 W bar appears beside it
    1.c  4.5-7.4  push        tighten on the gap between them
        → split: 4.5-6.0 / 6.0-7.4

BEAT 2 · scenes 3 · 5.6s
  Idea:     the chip itself
  Referent: object
  Source:   real search
  Image:    A2 — Loihi 2 die / package
  Query:    "Intel Loihi 2 neuromorphic chip die" — Intel newsroom press images
            [fallback: "Loihi 2 chip package photograph"]
  Shots:
    2.a  0.0-2.2  push        chip package held in frame
    2.b  2.2-4.0  detail crop hard cut — the die surface
    2.c  4.0-5.6  push        into the routing structure

BEAT 3 · scenes 4-5 · 9.2s
  Idea:     the flaw in normal processors — data crossing a bus
  Referent: unphotographable
  Source:   AI  (an architectural contrast; no single real figure carries it)
  Image:    A3 — memory block and compute block, traffic between them
  AI prompt: "two separated blocks connected by a single narrow channel, dense
             traffic moving back and forth along that channel, one block labelled
             by position as storage and the other as processing, single centred
             subject, clean space upper-left"
             Why AI: the von Neumann bottleneck is a schematic idea; published
             figures are textbook-specific and will not match the channel's look.
  Shots:
    3.a  0.0-2.4  push        the two blocks, gap between
    3.b  2.4-4.5  detail crop hard cut — the narrow channel, congested
    3.c  4.5-6.8  pan-right   traffic crossing left to right
    3.d  6.8-9.2  push        tighten on the choke point

BEAT 4 · scenes 6-8 · 11.0s
  Idea:     Loihi's answer — memory and compute co-located, event-driven
  Referent: unphotographable
  Source:   AI  (anchor-matched to A3 so the contrast reads instantly)
  Image:    A4 — mesh of nodes, each holding both functions, sparse activity
  AI prompt: "a dense grid of small identical nodes, each node containing both
             storage and processing together, only a scattered few nodes active
             while the rest stay dark, short connections between neighbours,
             single centred subject, clean space upper-left"
             Why AI: same as A3 — this is the deliberate visual rhyme against it.
  Shots:
    4.a  0.0-2.3  push        HARD CUT — the mesh, mostly dark
    4.b  2.3-4.4  detail crop hard cut — one node, both functions together
    4.c  4.4-6.5  pull        the full million-node field
    4.d  6.5-8.7  detail crop hard cut — a few nodes firing, rest dark
    4.e  8.7-11.0 push        the sparse firing pattern
        → split: 8.7-10.0 / 10.0-11.0

BEAT 5 · scenes 9-10 · 8.4s
  Idea:     the payoff number — around 100x on some workloads
  Referent: abstract
  Source:   graphic
  Image:    A5 — energy per operation, conventional vs Loihi
  Payload:  "~100x lower energy · some sensing and control workloads"
  Shots:
    5.a  0.0-2.4  push        the conventional bar
    5.b  2.4-4.6  pull        HARD CUT — Loihi's bar, nearly flat beside it
    5.c  4.6-6.5  detail crop hard cut — the qualifier, "some workloads"
    5.d  6.5-8.4  pull        both bars in frame

BEAT 6 · scenes 11-13 · 9.8s
  Idea:     the limits, then the question
  Referent: object
  Source:   real search  (anchor return)
  Image:    A2 — the chip again, wider
  Shots:
    6.a  0.0-2.2  push        HARD CUT back to the die
    6.b  2.2-4.3  pan-left    across the package
    6.c  4.3-6.4  pull        chip on a lab board, in context
    6.d  6.4-8.2  pull        wider, workbench scale
    6.e  8.2-9.8  push        settle on the chip under the CTA

CHECKS
  Shots: 26, longest 2.4s, avg 2.0s
  Images: 5 anchors (1 real reused twice, 2 AI, 2 graphics) — AI ratio 40%
  Anchor reuse: A2 in beats 2 and 6; A4 is a deliberate visual rhyme on A3
```

## What makes this plan work

- **Beat 5's qualifier gets its own shot.** The narration says *some* sensing and
  control tasks. Shot 5.c crops to that qualifier instead of letting "100x" stand
  unbounded. Visual honesty is a directing decision, not just a copy decision.
- **A3 and A4 are composed as a matched pair** — same framing, opposite structure.
  The contrast does the teaching; no caption needed.
- **40% AI is the ceiling, and it is justified here** because two beats are pure
  architecture. If a tech story exceeds this, the plan is under-researched — real
  die shots, wafer photos and lab rigs almost always exist.
- **Every number is a graphic, never an AI image.** Generating a picture of "100
  watts" would be inventing an image of a quantity.

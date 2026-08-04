# Evaluation — a strong plan and a weak plan for the same narration

Both plans below direct the **same** 48-second narration. The weak one is not a
strawman: it is the plan you get by default when you illustrate sentence by
sentence and reach for AI whenever a search feels slow. It contains the four
failures that actually ship.

## The narration (48s, 128 words)

> A tardigrade can survive being frozen to minus two hundred degrees, and it can
> survive the vacuum of space. In 2007, the European Space Agency put a batch of
> them on the outside of a satellite and left them in orbit for ten days. Most of
> them came back alive. They do it by drying out almost completely. The animal
> pulls water out of its own cells and replaces it with a sugar-like glass that
> holds every protein in place. Nothing moves, so nothing breaks. In that state
> it is not really alive, and it is not dead either. Add water, and it walks away
> within minutes. How long do you think one could stay like that?

---

## PLAN A — strong

```
HOOK
  Line:    "A tardigrade can survive being frozen to minus two hundred degrees,
            and it can survive the vacuum of space."
  Promise: the animal itself
  Frame 1: A1 — scanning electron micrograph of a tardigrade, filling the frame

VISUAL WORLD
  World:        one half-millimetre animal, and the states it can enter
  Anchors:      A1 tardigrade SEM · A2 tun state SEM · A3 BIOPAN/satellite ·
                A4 vitrified sugar glass
  Scale ladder: satellite in orbit → animal → cell → sugar matrix
  Palette:      off-white paper, cobalt contours, one amber state-change highlight
  Never show:   cartoon "water bear" mascots, plush toys, game renders, a
                tardigrade in a spacesuit, generic microscope-in-a-lab stock

BEAT 1 · scenes 1-2 · 8.4s · object · real search
  Image:  A1 tardigrade SEM
  Query:  "tardigrade scanning electron micrograph" — Wikimedia Commons
          [fallback: "Hypsibius dujardini SEM"]
  Shots:
    1.a 0.0-2.3 push        whole animal, claws visible
    1.b 2.3-4.4 detail crop hard cut — the claws
    1.c 4.4-6.4 pan-right   along the segmented body
    1.d 6.4-8.4 pull        animal small, scale implied

BEAT 2 · scenes 3-5 · 10.6s · object · real search
  Image:  A3 ESA BIOPAN exposure facility
  Query:  "ESA BIOPAN exposure facility Foton satellite" — ESA image archive
          [fallback: "space exposure experiment tray satellite exterior"]
  Shots:
    2.a 0.0-2.4 push        HARD CUT — the exposure tray
    2.b 2.4-4.5 detail crop hard cut — sample wells
    2.c 4.5-6.6 pull        tray on the satellite exterior
    2.d 6.6-8.6 pan-left    across the hull
    2.e 8.6-10.6 pull       satellite against Earth limb

BEAT 3 · scenes 6-8 · 11.2s · object · real search
  Image:  A2 tardigrade in tun state
  Query:  "tardigrade tun state desiccated SEM" — Wikimedia Commons
          [fallback: "tardigrade cryptobiosis tun micrograph"]
  Shots:
    3.a 0.0-2.3 push        HARD CUT — the contracted tun
    3.b 2.3-4.4 detail crop hard cut — collapsed body wall
    3.c 4.4-6.5 pull        tun beside A1's hydrated form, same scale
    3.d 6.5-8.7 push        back into the tun
    3.e 8.7-11.2 detail crop the sealed surface
        → split: 8.7-10.0 / 10.0-11.2

BEAT 4 · scenes 9-10 · 8.8s · unphotographable · AI
  Image:  A4 vitrified sugar matrix holding proteins
  AI prompt: "densely packed protein shapes suspended motionless inside a
             transparent glassy matrix, no gaps, everything locked in position,
             single centred subject, clean space upper-left"
             Why AI: intracellular vitrification has no public micrograph that
             reads at short-form scale.
  Shots:
    4.a 0.0-2.4 push        the matrix, proteins immobile
    4.b 2.4-4.5 detail crop hard cut — one locked protein
    4.c 4.5-6.6 pull        the full matrix
    4.d 6.6-8.8 push        stillness held

BEAT 5 · scenes 11-13 · 9.0s · object · real search (anchor return)
  Image:  A1 again — rehydrated, walking
  Shots:
    5.a 0.0-2.2 push        HARD CUT back to the hydrated animal
    5.b 2.2-4.3 detail crop hard cut — a leg mid-step
    5.c 4.3-6.5 pan-right   the animal moving
    5.d 6.5-9.0 pull        widest frame, holds under the CTA
        → split: 6.5-7.8 / 7.8-9.0

CHECKS
  Shots: 24, longest 2.5s, avg 2.0s
  Images: 4 anchors (3 real, 1 AI) — AI ratio 25%
  Anchor reuse: A1 opens and closes; A2 is composed against A1 at matched scale
```

---

## PLAN B — weak

```
HOOK
  Line:    same
  Frame 1: title card, "TARDIGRADES", starfield background

BEAT 1 · scene 1 · 3.5s   AI: "a cute water bear floating in outer space among stars"
BEAT 2 · scene 2 · 3.5s   AI: "a tardigrade frozen inside a block of blue ice, glowing"
BEAT 3 · scene 3 · 3.5s   AI: "a satellite orbiting earth, cinematic, dramatic lighting"
BEAT 4 · scene 4 · 3.5s   AI: "scientists in a lab looking at monitors"
BEAT 5 · scene 5 · 3.5s   AI: "a tardigrade waking up, triumphant"
BEAT 6 · scene 6 · 3.5s   AI: "microscopic view of cells drying out"
BEAT 7 · scene 7 · 3.5s   AI: "glowing sugar crystals, magical"
BEAT 8 · scene 8 · 3.5s   AI: "a water bear standing still, dramatic"
BEAT 9 · scene 9 · 3.5s   AI: "water droplet falling in slow motion"
BEAT 10 · scene 10 · 3.5s AI: "a happy tardigrade walking away, cartoon style"
BEAT 11 · scene 11 · 3.5s AI: "question mark over a starry background"

  All shots: single image, slow zoom in, crossfade to next
```

---

## Scoring

| # | Criterion | Plan A | Plan B |
|---|---|---|---|
| 1 | Frame 1 delivers the hook's subject | **2** — the animal, large | **0** — a title card |
| 2 | Beats, not sentences (5-8 beats) | **2** — 13 scenes → 5 beats | **0** — 11 scenes → 11 beats |
| 3 | Real imagery preferred; AI justified | **2** — 25% AI, justified | **0** — 100% AI, none justified |
| 4 | Every shot ≤2.5s | **2** — longest 2.5s | **0** — every shot 3.5s |
| 5 | Motion is varied and directed | **2** — push/pull/pan/crop, cut on the word | **0** — one zoom, crossfades |
| 6 | Coherent visual world + anchor reuse | **2** — A1 bookends, A1/A2 matched scale | **0** — 11 unrelated images |
| 7 | Scale ladder is deliberate | **2** — orbit → animal → cell → matrix | **0** — random |
| 8 | No off-world / mascot / stock imagery | **2** — explicitly excluded | **0** — "cute water bear", "cartoon style" |
| 9 | Abstract ideas routed to graphics, not fake photos | **2** — the one AI is a real mechanism | **0** — "magical sugar crystals" |
| 10 | Nothing asserted that the source does not support | **2** | **0** — "glowing", "magical", "happy" invent facts |
| | **Total /20** | **20** | **0** |

**Ship threshold: 15/20, with criteria 1, 4 and 8 each at 2.** A plan can be
interesting and still fail — those three are non-negotiable.

## Reading Plan B's failures

Plan B is what "make images for this script" produces unaided. Its four
structural failures:

1. **Sentence-by-sentence illustration.** 11 scenes became 11 images. Nothing
   holds long enough to be understood, and nothing recurs, so there is no story
   world — just a reel. This is failure mode 1 in `SKILL.md`.

2. **100% AI when most beats are photographable.** Tardigrade SEMs and the ESA
   BIOPAN facility are real, free, and better. Generating them is both worse
   looking and quietly dishonest — Plan B's "satellite" is not the satellite the
   experiment flew on.

3. **Every shot 3.5s with crossfades.** Over the 2.5s cap, so the renderer will
   split each one arbitrarily. The crossfades soften every transition, which is
   precisely the difference between "edited" and "assembled".

4. **Mascot and mood language.** "Cute", "cartoon style", "magical", "happy",
   "glowing" — each one either invents a fact or drags the piece toward the
   content-farm register the channel exists to avoid. Note also that "cartoon
   style" is a **style instruction inside a scene visual**, which fights
   `visual_styles.apply_style()` (failure mode 9).

Plan B scores 0 on criterion 10 for a reason worth stating plainly: a visual that
shows something the source does not support is a false claim, even though no
sentence in the narration changed.

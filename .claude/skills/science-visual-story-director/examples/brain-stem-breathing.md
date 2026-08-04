# Example — Brain stem: the cluster of neurons that sets your breathing rhythm

**Why read this one:** the hardest case in the whole channel. The subject is
internal, microscopic, and roughly the size of a grain of rice. Almost nothing
here can be photographed in a living person, and **anatomical precision is a
safety constraint, not a style preference** — a wrong location in a medical
visual teaches a false fact.

## Narration (51s, 136 words)

> You have never once decided to breathe. About seven thousand neurons in your
> brain stem do it for you. The cluster is called the pre-Bötzinger complex, and
> it sits in the medulla, just above where the spinal cord begins. It is roughly
> the size of a grain of rice. In 1991, researchers kept a slice of that tissue
> alive in a dish, and it kept generating a rhythm with no body attached and
> nothing to breathe. The rhythm is not coming from your lungs. It is coming from
> those cells. When a subset of them was silenced in animals, breathing became
> irregular and then stopped. That is also why opioids are dangerous: they act
> directly on this cluster. How often do you think about breathing now?

## Hook

```
Line:    "You have never once decided to breathe."
Promise: you — and something inside you doing the deciding
Frame 1: A1, a human head and neck in lateral view, brain stem highlighted in
         amber, everything else in restrained blue contour.
         Delivers: the "you" is a body, and the amber immediately points at the
         thing the story is about.
```

## Visual world

```
World:        one rice-grain cluster of cells in the medulla, and the rhythm it
              generates whether or not you are paying attention
Anchors:      A1 head/neck lateral with brain stem highlighted · A2 brain stem
              isolated · A3 pre-Bötzinger location · A4 slice-in-dish rig ·
              A5 rhythmic burst trace
Scale ladder: whole body → head and neck → brain stem → medulla → neuron cluster →
              voltage trace
Palette:      off-white paper, cobalt ink contours, one amber locator highlight,
              restrained red for the danger beat
Never show:   glowing blue "energy brain" wallpaper, a brain floating in space,
              lightning-bolt synapses, cartoon lungs with faces, a *cerebrum*
              standing in for the brain stem, any anatomy that puts the cluster
              in the wrong place
```

## The precision rule for this lane

Every scene in a body story where the narration states **where something is, how
big it is, or how it works** is a `precise_claim`. For those beats:

1. **Real anatomical figure first.** Wikimedia Commons anatomical plates, NIH/NLM
   Visible Human, the Allen Brain Atlas, open-access textbook figures.
2. If you must generate, generate the **orientation**, never the fine anatomy —
   a highlighted region on a clean lateral view, not a detailed labelled cutaway.
3. Never let an AI image be the sole source of a location claim.

Generative models place small brain-stem nuclei wrongly and confidently. An
amber blob in the right place beats a beautiful cutaway in the wrong one.

## Beat plan

```
BEAT 1 · scenes 1-2 · 7.6s
  Idea:     it happens without you, and it happens in there
  Referent: object          (anatomy is photographable/illustrated from real atlases)
  Source:   real figure
  Image:    A1 — head and neck lateral, brain stem region highlighted
  Query:    "brainstem anatomy lateral view medulla pons" — Wikimedia Commons /
            Gray's Anatomy plates
            [fallback: "human brainstem diagram sagittal"]
  Shots:
    1.a  0.0-2.3  push        head and neck, amber region already visible
    1.b  2.3-4.5  detail crop hard cut — the highlighted brain stem
    1.c  4.5-7.6  push        tighten toward the medulla
        → split: 4.5-6.0 / 6.0-7.6

BEAT 2 · scenes 3-5 · 10.4s
  Idea:     naming and locating it — pre-Bötzinger, in the medulla, rice-sized
  Referent: object
  Source:   real figure + graphic   (precise_claim: location AND size)
  Image:    A2 brain stem isolated → A3 location marker
  Query:    "medulla oblongata anatomy labelled" — NIH / Wikimedia Commons
            [fallback: "brainstem medulla cross section atlas"]
  Payload:  scale graphic — "≈ a grain of rice · ~7,000 neurons"
  Shots:
    2.a  0.0-2.2  push        HARD CUT — brain stem isolated on paper
    2.b  2.2-4.4  detail crop hard cut — upper medulla, above the cord junction
    2.c  4.4-6.5  push        the marked cluster position
    2.d  6.5-8.4  pull        HARD CUT — rice grain beside it, true relative size
    2.e  8.4-10.4 push        tighten on the grain-to-cluster comparison

BEAT 3 · scenes 6-8 · 11.2s
  Idea:     the 1991 experiment — a slice kept rhythmic in a dish
  Referent: object
  Source:   real search      (the rig is genuinely photographable)
  Image:    A4 — brain slice in a recording chamber
  Query:    "brain slice electrophysiology recording chamber" — Wikimedia Commons,
            NIH image gallery
            [fallback: "acute brain slice perfusion chamber electrode"]
  Shots:
    3.a  0.0-2.3  push        HARD CUT — the slice in its chamber
    3.b  2.3-4.4  detail crop hard cut — the electrode contacting tissue
    3.c  4.4-6.5  pan-right   across the perfusion rig
    3.d  6.5-8.7  detail crop hard cut — the slice itself, no body attached
    3.e  8.7-11.2 pull        the whole bench setup
        → split: 8.7-10.0 / 10.0-11.2

BEAT 4 · scenes 9-10 · 8.6s
  Idea:     the rhythm is in the cells — here it is, as a signal
  Referent: unphotographable
  Source:   real figure      (published burst recordings exist; use data, not art)
  Image:    A5 — rhythmic population burst trace
  Query:    "pre-Botzinger complex rhythmic burst recording trace" — open-access
            figure (J Neurophysiol / eLife)
            [fallback: "neuronal population burst electrophysiology trace"]
  Shots:
    4.a  0.0-2.4  pan-right   along the trace, bursts arriving
    4.b  2.4-4.5  detail crop hard cut — one burst envelope
    4.c  4.5-6.6  push        the regular interval between bursts
    4.d  6.6-8.6  pull        the full rhythmic train

BEAT 5 · scenes 11-12 · 8.8s
  Idea:     silence those cells and breathing fails
  Referent: abstract
  Source:   graphic          (do NOT illustrate an animal dying)
  Image:    the same trace, degrading — regular → irregular → flat
  Payload:  "subset silenced → irregular → stopped"
  Shots:
    5.a  0.0-2.3  push        HARD CUT — regular bursts, as in beat 4
    5.b  2.3-4.4  pan-right   intervals becoming uneven
    5.c  4.4-6.5  detail crop hard cut — a missed burst
    5.d  6.5-8.8  pull        the trace flat, red accent enters
        → split: 6.5-7.8 / 7.8-8.8

BEAT 6 · scenes 13-14 · 9.2s
  Idea:     why opioids are dangerous, then the question
  Referent: unphotographable
  Source:   AI              (receptor action on the cluster; orientation only)
  Image:    A6 — molecules binding at the marked cluster site
  AI prompt: "small molecules settling onto a marked region within a brain stem
             viewed from the side, the region already indicated by a highlight,
             the rest of the structure in plain contour, single centred subject,
             clean space upper-left"
             Why AI: no open figure shows opioid receptor action localised to this
             cluster in the channel's visual language. Generating **orientation
             only** — the location comes from A3, not from the model.
  Shots:
    6.a  0.0-2.2  push        HARD CUT — molecules approaching the marked site
    6.b  2.2-4.3  detail crop hard cut — binding at the highlight
    6.c  4.3-6.4  pull        brain stem whole again
    6.d  6.4-9.2  pull        RETURN to A1, head and neck, widest, under the CTA
        → split: 6.4-7.8 / 7.8-9.2

CHECKS
  Shots: 30, longest 2.5s, avg 1.9s
  Images: 6 anchors (4 real figures, 1 AI, 1 graphic pair) — AI ratio 17%
  Anchor reuse: A1 opens and closes; A5's trace returns degraded in beat 5
```

## What makes this plan work

- **The location claim never rests on a generated image.** A1-A3 come from real
  anatomical atlases. The single AI image in beat 6 inherits its position from the
  already-established highlight — it generates *orientation*, not anatomy.
- **Beat 5 reuses beat 4's trace instead of illustrating harm.** The strongest and
  most tasteful way to show breathing failing is the signal the viewer has already
  learned to read, degrading. No animal imagery, no distress imagery.
- **The rice grain is a real object at true relative scale.** Concrete comparisons
  beat adjectives; "small" is not a picture, a grain of rice is.
- **Amber is reserved for one job all video: *this is the thing*.** Colour used as
  a consistent locator is what makes six images feel like one anatomy lesson.
- **14 scenes → 6 beats.** The sentences naming the cluster, its position and its
  size are one picture at three crops, not three pictures.

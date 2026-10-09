# Example — Space: an exoplanet atmosphere result

**Why read this one:** the common case. Most beats route to real imagery, and the
plan is carried by a clean scale ladder from starfield down to a molecule.

## Narration (52s, 138 words)

> This planet just changed the search for life. K2-18b orbits a red dwarf
> 124 light years away, in the habitable zone. It is eight times the mass of
> Earth. When Webb watched it pass in front of its star, the starlight came
> through the atmosphere first. Different molecules absorb different colours, so
> that light carries a chemical fingerprint. Webb found methane and carbon
> dioxide. It also found a weak hint of dimethyl sulfide, a molecule that on
> Earth is made almost entirely by marine plankton. That signal is not confirmed.
> It sits near the edge of what Webb can currently resolve, and it may vanish
> with more observation time. But if it holds, it would be the first chemical
> hint of life outside the solar system. Would you call that proof?

## Hook

```
Line:    "This planet just changed the search for life."
Promise: a planet — one specific world, large in frame
Frame 1: A1, the K2-18b artist's impression, planet filling 70% of frame,
         terminator line vertical. Delivers: the subject is the planet itself,
         not a telescope and not a scientist.
```

## Visual world

```
World:        a single super-Earth and the starlight that passes through its air
Anchors:      A1 K2-18b disc · A2 JWST observatory · A3 transmission spectrum ·
              A4 DMS molecule
Scale ladder: starfield → planetary disc → atmospheric limb → spectrum → molecule
Palette:      deep indigo, cobalt ink, one warm amber highlight, off-white paper
Never show:   green cartoon aliens, UFOs, game-engine planet renders, generic
              "scientist points at screen" stock, Earth standing in for K2-18b
```

## Beat plan

```
BEAT 1 · scenes 1-2 · 6.0s
  Idea:     this specific world, and where it sits
  Referent: object
  Source:   real search
  Image:    A1 — K2-18b artist's impression against its red dwarf
  Query:    "K2-18b exoplanet artist impression" — ESA/Hubble, NASA
            [fallback: "K2-18b Webb illustration"]
  Shots:
    1.a  0.0-2.2  push        planet disc, terminator centre-right
    1.b  2.2-4.1  detail crop hard cut — the red dwarf small at frame edge
    1.c  4.1-6.0  pull        disc shrinks, habitable-zone band implied

BEAT 2 · scenes 3-4 · 8.4s
  Idea:     the transit — starlight passing through air before reaching us
  Referent: object
  Source:   real search
  Image:    A2 — JWST, then transit geometry diagram
  Query:    "James Webb Space Telescope full mirror deployed" — NASA
            [fallback: "JWST sunshield full view NASA"]
  Shots:
    2.a  0.0-2.4  push        gold mirror segments fill frame
    2.b  2.4-4.5  pan-right   across the segment array
    2.c  4.5-6.5  detail crop hard cut — planet crossing the stellar disc
    2.d  6.5-8.4  push        tighten on the lit atmospheric ring

BEAT 3 · scenes 5-6 · 7.8s
  Idea:     the fingerprint — molecules absorb specific colours
  Referent: unphotographable
  Source:   real figure  (a spectrum is real published data, not a concept)
  Image:    A3 — JWST transmission spectrum of K2-18b
  Query:    "K2-18b JWST transmission spectrum methane CO2" — NASA/STScI figure
            [fallback: "exoplanet transmission spectrum absorption features"]
  Shots:
    3.a  0.0-2.3  pan-right   left to right along the wavelength axis
    3.b  2.3-4.4  detail crop hard cut — the methane absorption dip
    3.c  4.4-6.2  detail crop hard cut — the CO2 dip
    3.d  6.2-7.8  pull        both dips together in one frame

BEAT 4 · scenes 7-8 · 9.6s
  Idea:     the DMS hint, and what makes it on Earth
  Referent: unphotographable → object
  Source:   AI, then real search
  Image:    A4 — DMS molecular structure; then real plankton micrograph
  AI prompt: "a simple molecular structure of dimethyl sulfide, one sulfur atom
             bonded to two methyl groups, single centred subject, clean space
             upper-left"
             Why AI: a specific labelled DMS structure keyed to this story's
             palette is not available as a reusable public figure; the molecule
             itself is not photographable.
  Query:    "marine phytoplankton bloom micrograph" — NOAA, Wikimedia Commons
  Shots:
    4.a  0.0-2.2  push        molecule centre-right, sulfur atom emphasised
    4.b  2.2-4.3  detail crop hard cut — the sulfur bond
    4.c  4.3-6.4  push        HARD CUT to plankton micrograph, cells filling frame
    4.d  6.4-8.2  pan-left    across the bloom
    4.e  8.2-9.6  pull        bloom becomes ocean-surface scale

BEAT 5 · scenes 9-11 · 10.8s
  Idea:     the caveat — the signal is weak and may vanish
  Referent: abstract
  Source:   graphic
  Image:    confidence graphic — the DMS feature against the noise floor
  Payload:  "not confirmed · near Webb's resolution limit"
  Shots:
    5.a  0.0-2.4  push        the weak feature, barely above noise
    5.b  2.4-4.6  detail crop hard cut — error bars crossing the feature
    5.c  4.6-6.8  pull        feature shrinks into the full noisy spectrum
    5.d  6.8-8.9  push        RETURN to A3, spectrum, now read as uncertain
    5.e  8.9-10.8 pull        widen off the spectrum

BEAT 6 · scenes 12-13 · 9.4s
  Idea:     what it would mean if it holds, then the question
  Referent: object
  Source:   real search  (anchor return)
  Image:    A1 — K2-18b again, widest framing of the video
  Shots:
    6.a  0.0-2.3  push        planet disc returns, mid framing
    6.b  2.3-4.4  detail crop hard cut — atmospheric limb, thin and lit
    6.c  4.4-6.6  pull        planet against the starfield
    6.d  6.6-9.4  pull        widest frame of the video, holds under the CTA

CHECKS
  Shots: 25, longest 2.8s → split 6.d into 6.d 6.6-8.0 / 6.e 8.0-9.4, longest 2.4s
  Images: 6 anchors (5 real, 1 AI) — AI ratio 17%
  Anchor reuse: A1 opens and closes; A3 returns in beat 5 recontextualised
```

## What makes this plan work

- **Scale ladder is monotonic** through beats 1-4 (planet → telescope → spectrum
  → molecule), then beat 6 climbs back out. The viewer always knows where they are.
- **A1 bookends.** The video ends on the image it opened with, wider. That is the
  cheapest available continuity device and it costs one extra image: zero.
- **One AI image out of six**, and it is the only genuinely unphotographable
  subject that lacks a reusable published figure.
- **Beat 5 reuses A3 rather than generating a "doubt" image.** Recontextualising an
  existing anchor is stronger than inventing a new picture for an abstract idea.
- **13 scenes became 6 beats.** The narration's sentences about mass and distance
  are the *same picture* as the planet — detail crops, not new images.

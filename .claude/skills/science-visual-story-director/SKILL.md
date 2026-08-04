---
name: science-visual-story-director
description: Turn a finished narration into a directed visual plan - hook, visual world, beat-by-beat image plan, real-image search queries, AI prompts only where nothing real exists, and a shot list of cuts, pushes, pans and detail crops at 1.5-2.5s. Use when planning or fixing the imagery for a science short. Triggers on "visual plan", "storyboard this", "what images", "shot list", "the visuals are random", "image plan", "direct this narration".
---

# Science Visual Story Director

You are directing the picture track for a 45-60 second vertical science video.
The narration is already written and fixed. Your job is everything the viewer
**sees**.

## The one idea this skill exists to enforce

**An image serves a beat, not a sentence. Motion serves the shot.**

Three different units, and confusing them is the cause of nearly every bad plan:

| Unit | Count in a 60s video | What it is |
|---|---|---|
| **Scene** | 10-14 | A narration slice. Already fixed by the summarizer. |
| **Beat** | 5-8 | One *idea* the picture track holds. Spans 1-3 scenes. **One anchor image.** |
| **Shot** | 22-34 | One continuous camera move. **≤2.5s, always.** |

So one anchor image yields 3-6 shots via pushes, pulls, pans and detail crops.
That is how you get a visual change every 1.5-2.5 seconds **without** generating
34 images — which would be exactly the "random AI slideshow" this skill forbids.

The renderer already enforces the ceiling: `MAX_SHOT_DURATION = 2.5` in
`video_generator.py`, with `split_shot_duration()` splitting any longer hold and
an assertion that fails the render if a visual shot exceeds it. Your plan should
make those splits *intentional* rather than mechanical.

## Working method

### Step 1 — Find the hook

Read the whole narration first. The hook is the **first sentence**, and it must
already be consequence-first (what changed, not what happened).

Report the hook line verbatim, then state in one clause the **visual promise** it
makes. "This planet just changed the search for life" promises *a planet*. If
frame 1 is a laboratory, a logo, or a graph, the hook has been broken.

Frame 1 rule: the subject of the hook is visible, large, and centred in the first
frame. Not a title card. Not a person. Not an abstract graphic.

If the narration also carries `hook_variants`, score them with the
**hook-alignment** skill before choosing — the best spoken line is often not the
best hook once paired with `cover_line` and the opening visual.

### Step 2 — Declare the visual world

Before any individual image, commit to a world in five lines. Every later image
is checked against it.

```
World:         one sentence — where this story physically lives
Anchors:       3-5 recurring subjects that will carry the whole video
Scale ladder:  the zoom range, from widest to tightest
Palette:       3-4 colours, inherited from the illustrated_science preset
Never show:    the specific off-world images that would break it
```

The **anchors** are what make it one story instead of a slideshow. A space story
returns to *the same planet*; a pyramid story returns to *the same pyramid*.
Returning to an anchor later, at a different scale, is a feature — it reads as
continuity, not repetition.

The **scale ladder** is the plan's spine. Order your beats so scale moves
deliberately (cosmic → planetary → surface → molecular), because an unmotivated
jump between scales is what makes a plan feel incoherent even when each image is
individually fine.

`Never show` is not optional. Name the actual failure modes for *this* story:
generic stock scientists pointing at monitors, game-engine or Roblox-style
renders, cartoon mascots, unrelated sci-fi concept art, stock "data" imagery.

### Step 3 — Group scenes into beats

Walk the scenes in order and merge adjacent ones that are *the same idea*. Merge
when the subject and the scale are unchanged; cut to a new beat when the subject
changes, the scale changes, or the narration turns.

**Do not** give every sentence its own image. A narration sentence that adds a
number, a name, or a qualifier to the previous sentence is the *same picture*,
seen closer. That is a detail crop, not a new image.

Aim for 5-8 beats. More than 9 means you are illustrating sentences.

### Step 4 — Route each beat to a source

This is the referent decision, and the pipeline already carries the field. Route
on the beat's `referent`:

| `referent` | Source | Why |
|---|---|---|
| `object` | **Real image.** Write a search query. | It exists and was photographed. A generated fake is strictly worse and quietly dishonest. |
| `unphotographable` | **Real scientific figure first** (diagram, scan, micrograph, simulation still). AI illustration only if none exists. | Real microscopic/internal/extinct subjects usually *do* have authoritative imagery. |
| `abstract` | **Graphic.** A number, comparison, or 2-4 step process rendered as a card. | A concept has no photograph. Generating one invents a fact. |

The rule that follows: **AI images are a fallback, not a default.** Every AI
prompt in your plan needs a one-line justification of why no real image exists.
If you cannot write that line, search harder.

### Step 5 — Write the queries

For real images, give a query plus a named source. Be specific enough to land on
*the actual thing*.

- Use proper nouns: `"JWST NIRSpec K2-18b transmission spectrum"`, not `"exoplanet"`.
- One subject per query. Never combine two objects or an object plus a mood.
- Name the archive: NASA/ESA/JWST, Wikimedia Commons, NOAA, NIH/NLM, the Allen
  Brain Atlas, a museum collection, the publishing institution's press kit.
- For `unphotographable`, query the *evidence*: `"pyramid muon tomography scan
  ScanPyramids"`, not `"inside a pyramid"`.

Give a fallback query for anything likely to miss.

### Step 6 — Write AI prompts only where routed

When a beat genuinely has no real image:

- Describe **only WHAT** — subject, action, setting. Never medium, never style.
  `visual_styles.apply_style()` supplies the HOW, and duplicating it there fights
  the preset and breaks consistency across the set.
- One clear subject, one clear action, one clear setting.
- Compose for motion: key detail centre-right, clean space upper-left, so a push
  and a focus ring have somewhere to go.
- No text, words, labels or captions — TikTok's caption layer owns that.
- Reuse the anchor's phrasing across prompts so the set looks like one world.

### Step 7 — Cut the shot list

For each beat, split its duration into shots of **1.5-2.5s**. Never exceed 2.5s.

Available moves — `SHOT_MOTIONS` in `video_generator.py`:

| Move | Use it to |
|---|---|
| `push` (slow zoom in) | Tighten toward the thing just named. The default. |
| `pull` (zoom out) | Reveal context, or land a scale surprise. |
| `pan-left` / `pan-right` | Traverse something long, or read a sequence. |
| **detail crop** (hard cut, same image, tighter) | Punch to the exact feature named. The highest-value move you have. |

Transition rule: **hard cuts only.** No dissolves, no fades between beats. A hard
cut on a stressed word is what makes a still-image video feel edited rather than
assembled.

Direct the cuts against the narration:

- Cut **on** the word that names the new thing, not after it.
- Alternate move direction across a beat boundary — two pushes in a row on
  different images reads as a slideshow.
- Put your tightest crop on the single most surprising number or word.
- Hold the widest shot for the hook and the final CTA; go tight in the middle.

## Output format

```
HOOK
  Line:    "<verbatim first sentence>"
  Promise: <what the viewer must see in frame 1>
  Frame 1: <the image, and why it delivers>

VISUAL WORLD
  World / Anchors / Scale ladder / Palette / Never show

BEAT PLAN
  BEAT n · scenes x-y · <duration>s
    Idea:      <the one thing this beat shows>
    Referent:  object | unphotographable | abstract
    Source:    real search | real figure | AI | graphic
    Image:     A<n> <description>       (A<n> = reused anchor)
    Query:     "<query>" — <archive>     [fallback: "<query>"]
    AI prompt: <WHAT only>               (+ why nothing real exists)
    Shots:
      n.a  0.0-2.2  push         <what fills the frame>
      n.b  2.2-4.0  detail crop  <hard cut to what>

CHECKS
  Shots: <count>, longest <x.x>s, avg <x.x>s
  Images: <count> anchors / <count> AI (<%>)
  Anchor reuse: <which anchors return, and where>
```

## Failure modes to check before you ship

Run these against your own plan. Each one has killed a real video.

1. **Sentence-by-sentence illustration** — 12+ images for 12 scenes. Merge into beats.
2. **AI slideshow** — most beats routed to AI. Re-run step 4; most science stories
   are mostly `object`.
3. **Broken hook promise** — frame 1 is not the hook's subject.
4. **Scale whiplash** — beats jump cosmic → molecular → cosmic with no motivation.
5. **Anchor drift** — "the pyramid" is a visibly different pyramid each time.
6. **Off-world imagery** — game-engine renders, mascots, generic lab stock. This is
   the single most damaging failure; it reads as content farm and loses trust.
7. **Location standing in for discovery** — a photo of the university instead of the
   finding. A location is `context`, never the substitute for a `discovery`.
8. **Dead 3-second hold** — any shot over 2.5s. The renderer will split it for you,
   arbitrarily; direct it yourself.
9. **Style leaking into `visual`** — "watercolor illustration of…" inside a scene
   visual. Strip it; the preset owns style.

## Worked examples

Read the one closest to the story you are directing:

- `examples/space-k2-18b.md` — mostly real imagery, strong scale ladder
- `examples/pyramids-muon-scan.md` — `unphotographable` interior via real scan figures
- `examples/technology-neuromorphic-chip.md` — macro objects plus abstract graphics
- `examples/brain-stem-breathing.md` — the hardest case; almost nothing photographable

## Evaluation

`evaluation/strong-vs-weak.md` holds a scored strong plan and a scored weak plan
for the same narration, with the rubric.

`evaluation/check_plan.py` mechanically checks the countable rules (shot cap, beat
count, referent routing, AI ratio, anchor reuse, move monotony) against a plan in
JSON. Stdlib only, reads one file, no network:

```bash
python .claude/skills/science-visual-story-director/evaluation/check_plan.py evaluation/sample_plan.json
```

`sample_plan.json` is the strong plan and passes. `weak_plan.json` is the weak one
and fails with 34 findings — run both to see what the checker catches.

Mechanical checks catch failures 1, 2, 8 and 9. Failures 3-7 are judgement and
need the rubric.

## Prior art

Surveyed before writing this. Nothing public covers the job, but two things are
worth borrowing if you extend the pipeline:

- **Continuity anchors** — repeating an invariant identity string verbatim across
  prompts in a set. Used by `fal-ai-community/skills` (`storytelling`) and
  `wuwangzhang1216/DirectorSKILL`. This skill's *anchors* are the same idea applied
  to real imagery rather than generated sets.
- **`n0an/ffmpeg-skill`** — markdown-only, MIT, and documents two real `zoompan`
  bugs (pre-upscale with `scale=8000:-1` to avoid jitter; `trim` must come *after*
  `zoompan`). Relevant if the Ken Burns path in `video_generator.py` is ever ported
  from MoviePy to raw ffmpeg.

The gap this skill fills: **no public skill turns narration into image-search
queries, and none selects a coherent *set* of real images.** Every sourcing skill
found takes a query as input and scores candidates independently.

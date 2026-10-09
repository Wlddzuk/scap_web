# SCAP visual direction: Pixel Night Lab

Status: **route locked 2026-10-06** (user-approved). The proof set below is round 2.
Owner: SCAP. Deliverable: 9:16 narrated science shorts for TikTok, Reels and Shorts.
Decision history: `ROUTES.md` (routes A and C rejected; kept for the record).

## Thesis

Science as a night-time adventure game: a glowing pixel Moss explores a dark universe
in which every invisible force the narration names is drawn as neon light.

## Invariants (every frame, every video)

| Element | Rule | Failure signal |
|---|---|---|
| Medium | Chunky 16-bit pixel art: crisp grid, no anti-alias blur | Smooth vector or painterly shading, photo texture |
| Ground | Solid flat deep navy `#0B1026` with sparse twinkling pixel stars | Checkerboard, gradient banding, white or light grounds |
| Forces | Anything invisible (fields, flows, signals, heat) is a neon line or stream in magenta or amber | A mechanism named in the narration with nothing showing it |
| Focal brightness | Moss (or the hero object in scenes without Moss) is the brightest shape | Background details out-shining the subject |
| Caption band | 62–85% of frame height holds only dark ground or plain shadow | Moss, key objects or bright lines in the band |
| Text | No text, letters, numbers or labels in generated images; the renderer owns all type | Any lettering, including gibberish |
| One idea | One subject and one mechanism per frame | Two competing focal points |

## Controlled variables

- **Moss's presence:** in the hook, then at least one scene in every three. The other
  scenes show the subject alone, in the same style.
- **Moss's scale:** 15–45% of frame height. Smaller for cosmic scale, larger for reactions.
- **Moss's placement:** upper half or side thirds; never the caption band.
- **Expression:** neutral, thinking, amazed or delighted (see the bible).
- **Accent balance:** magenta is the primary force colour and amber the secondary or
  comparison colour. A third hue is allowed only for natural objects (blue ice, green-blue Earth).

## Anti-patterns

- Spiral galaxies standing in for protoplanetary disks; planets drawn before they formed.
- Planet rings used to depict magnetic fields (fields are dipole loops).
- A glow outline around Moss (round-2 "delighted" drift).
- Real photographs inside a pixel video, which break the world.
- Scenes with no Moss and no force line for more than two consecutive beats.

## Tokens (DTCG 2025.10)

```json
{
  "color": {
    "$type": "color",
    "ground":        { "$value": { "colorSpace": "srgb", "components": [0.043, 0.063, 0.149], "hex": "#0B1026" } },
    "force-primary": { "$value": { "colorSpace": "srgb", "components": [1.0, 0.239, 0.659], "hex": "#FF3DA8" } },
    "force-second":  { "$value": { "colorSpace": "srgb", "components": [1.0, 0.690, 0.0], "hex": "#FFB000" } },
    "moss-body":     { "$value": { "colorSpace": "srgb", "components": [0.620, 0.851, 0.765], "hex": "#9ED9C3" } },
    "caption-text":  { "$value": { "colorSpace": "srgb", "components": [1.0, 1.0, 1.0], "hex": "#FFFFFF" } },
    "caption-active":{ "$value": { "colorSpace": "srgb", "components": [1.0, 0.847, 0.302], "hex": "#FFD84D" } }
  },
  "layout": {
    "$type": "dimension",
    "caption-band-top":    { "$value": { "value": 1190, "unit": "px" } },
    "caption-band-bottom": { "$value": { "value": 1632, "unit": "px" } }
  }
}
```

The caption tokens match the renderer today (`CAPTION_ACTIVE_COLOR`, white text with a
black stroke). On the navy ground both pass WCAG large-text contrast by a wide margin.
The band values are proof-set starting points for a 1080×1920 frame.

## Mascot bible: Moss (canonical: `assets/moss/moss_canonical.png`; JPEG copies of the reference set are in `pixel-night-lab/moss/`)

- **Identity:** an original cartoon tardigrade; a tiny, unflappable explorer; curious,
  deadpan-amazed, never frightened.
- **Locked:** an upright bean body with three soft creases; short hind feet; two short
  front limbs with tiny claws; mint-sage `#9ED9C3` with a lighter belly; two black bead
  eyes; a small round mouth; one pair of brass goggles with amber lenses.
- **Flexible:** pose, scale, expression, and goggles up (default) or down.
- **Forbidden:** teeth, hands or fingers, clothing besides the goggles, extra
  accessories, a glow outline, a second pair of goggles, a realistic microscope texture.
- **Approved references:** `moss_canonical`, `moss_front`, `moss_side`, `moss_thinking`.
  `moss_amazed` drifted (goggles stayed up) and `moss_delighted` drifted (glow outline):
  do not use them as references until they are rerolled.
- **Rights:** an original fictional mascot, distinct from the reference creator's character.

## Prompt blocks

The canonical copies live in `mascot_style.py` (`MOSS`, `STYLE`, `CAPTION_BAND`,
`FORCES`, `NO_MASCOT`, `AVOID`); `pixel-night-lab/generate_round2.py` holds the round-2
versions for the record. Change the code and this guide together.

- **Moss scenes** (hook, then every third): `fal-ai/nano-banana/edit` with
  `assets/moss/moss_canonical.png` attached.
- **Other scenes:** `fal-ai/nano-banana` text-to-image with **no** reference. Given
  Moss's image and told to use it "for style only", the edit model drew him anyway,
  without his goggles.
- **No hex colour codes in prompts:** the model printed "FFBDA0" into a frame. Name colours instead.

## Continuity ledger (round 2)

| Frame | Result | Notes |
|---|---|---|
| story/1_hook | Pass (rerolled) | The first take stacked two pairs of goggles. The reroll kept the goggles up despite the prompt, and the rock reads a little like cheese. |
| story/2_antarctica | Pass | Ice edges into the caption band; captions remain readable. |
| story/3_disk | Pass | A correct flat disk with no spiral arms; force lines pull dust inward. |
| story/4_compare | Pass (rerolled) | The first take gave Earth a ring and sat in the caption band. |

## First pipeline render (article 82, 2026-10-06)

10/10 scenes generated with no fallbacks; the image step took 24s and the full render
252s. It failed on lettering (a hex code and a monitor reading "41.403"), a Saturn-style
ring for a magnetic field, Moss leaking into a no-Moss scene, and one split-panel frame.
Each has a prompt or routing fix (above); targeted regenerations of the three scenes
confirmed the fixes. The text-only scenes still drift in palette (green bars, lighter
navy), so the palette rule was added; confirm it on the next full render.

## Confirmation renders (2026-10-06, articles 82 and 81/CRISPR)

19/19 scenes generated, about 3.2 min per render. The biology story reads well in
pixel art (antibody Y-shapes, red cells, a shield), so the style is not limited to
space. Failures found, and fixes made:

| Failure | Cause | Fix |
|---|---|---|
| "Moss" lettered on a cloud; look-alike mascots (cyan blob, green creature with sunglasses) | The shared STYLE and AVOID blocks named Moss and his goggles | Shared blocks never mention Moss; Moss-only rules live in `MOSS_AVOID` |
| "DAY 0 / DAY 28", "Earth's Magnetic Field" labels | Summarizer visuals are written as charts and timelines; image models obey them literally | `physicalize_visual()` rewrites infographic visuals into wordless physical scenes (Groq) |
| Trees and a forest standing in for engraftment | The first rewrite prompt allowed metaphors | Rewrite must use the literal science objects in the narration |
| Grey human hands | Rewrites introduced hands | "Human hands or arms" banned in the shared AVOID |
| Moss's head cut off | Archive-photo crops zoom 1.3x toward the top | `MASCOT_FRAMINGS` anchor zooms at the top edge |
| Truncated rewrites | gpt-oss spends reasoning tokens from max_tokens | 400-token budget; rewrites without final punctuation are rejected |

**Open risk:** the rewrite model can introduce science errors (one rewrite called the
CD33-deleted donor cells "leukemic"). The real fix is upstream: have the summarizer,
which read the article, write physical and wordless visuals in the first place, and
keep the rewrite as a safety net.

## Cost model (2026-10-06)

| Scene type | Model | Price | Prompt |
|---|---|---|---|
| Moss scenes (hook, every third) | `fal-ai/nano-banana/edit` + `moss_canonical.png` | ~$0.039 | Full blocks; this model follows "avoid" lists |
| All other scenes | `fal-ai/z-image/turbo` | ~$0.005 | `PIXEL_LOOK`, positive-only |

That is about **$0.19 per 11-scene video**, down from $0.43 with Nano Banana everywhere.
Z-Image has no negative prompt, so every noun in its prompt gets drawn: "never planets
or rings" produced a ringed planet over a lab bench. Keep its prompt free of
forbidden nouns; space rules are added only when the scene is about space.

**Final render (CRISPR, 2026-10-06):** no text, no impostors, and the biology reads as
cells. Remaining defect: Nano Banana places Moss in the caption band despite the
rule, so captions overlap his body. Next step: draw Moss as a code-composited sprite
(fixed position, perfect continuity, about $0.06 per video).

## Moss becomes an animated sprite (2026-10-06)

Moss is no longer painted into scenes. Painted Moss drifted, covered the
captions, and needed the $0.039 edit model. He is now a sprite layer animated in
code (`moss_sprite.py`), cut from approved poses in `assets/moss/sprites/`
(canonical, front, side, thinking, amazed, point, wave, delighted, blink,
walk_a, walk_b). He is about 12% of the frame height (half the first prototype),
stands with his feet above the caption band, walks in and out, idles and
blinks, and reacts to the narration's word timings. Every scene now uses
Z-Image Turbo, so a video costs about **$0.06 in images**.

Known pose gaps: `amazed` keeps the goggles up, and the walk frames differ only
slightly. Horizontal movement and the bob carry the walk.

## Acceptance tests for pipeline output

1. Extract a frame every 2s from a render; Moss appears in the hook and in at least a
   third of the scenes.
2. No frame has bright content inside the caption band (check visually with captions on).
3. No lettering in any generated frame.
4. Every scene that names a force shows it as a neon line or stream.
5. Moss passes the bible at thumbnail size: silhouette, goggles, colour.

## Spend

Round 1 (routes): 9 × ~$0.039. Round 2 (references and story): 12 × ~$0.039 (10 plus 2
rerolls). About $0.82 in total on `fal-ai/nano-banana`.

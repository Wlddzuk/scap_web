# SCAP style lock — routes and mascot (round 1)

Status: **Route B (Pixel Night Lab) chosen 2026-10-06.** See STYLE.md. Routes A and C are kept as decision history.
Method: `visual-style-direction` (contrastive routes) and `character-design-continuity`
(mascot bible, reference-first generation). Proofs: `proofs/`, board: `style_lock_board.jpg`.
Proof story: article 82, "Ancient meteorites reveal a powerful force that helped build the
Solar System". Every route renders the same two beats so only the style differs.

## Brief

- **Audience:** curious scrollers on TikTok, Reels and Shorts; no science background assumed.
- **Job:** stop the scroll in the first frame, then make one invisible idea (a magnetic
  field, a mechanism) readable on a phone in about 2 seconds per shot.
- **Constraints:** 9:16; images carry no text, because captions and headlines are burned
  in by the renderer; the cost per video should stay in the cents; every scene is
  generated automatically, so the look must survive AI drift.
- **Recognition device:** one recurring mascot, so that a viewer knows the account before
  reading a word.

## Reference ledger

| Reference | Mechanism adopted | Identity details excluded |
|---|---|---|
| @divyannshisharma carousel (user-supplied) | One recurring mascot per post, one idea and one prop per frame, a calm uniform ground | Their orange blocky pixel creature, its hats and props, the white card layout, and the bold black headline typography |
| @thoughtpalace / @hellopersonality (user-supplied) | A single locked palette repeated daily at volume becomes the brand | Anime women, the blue/red duotone, ornate headdresses |

## Mascot bible: Moss (draft, extracted from the round-1 canonical takes)

- **Codename:** Moss. **Species:** an original cartoon tardigrade ("moss piglet"),
  the animal famous for surviving the vacuum of space.
- **Role:** a tiny, unflappable explorer; curious, deadpan-amazed, never frightened, never manic.
- **Silhouette (locked):** a plump upright bean with three soft segment creases, standing
  on short hind feet, with two short front limbs ending in tiny claws. The model reads
  the anchor this way in all three routes, so the bible follows the canonical take
  instead of fighting for eight legs.
- **Face (locked):** two small black bead eyes, a small round "o" mouth or a gentle smile, no eyebrows.
- **Colour (locked):** mint-sage body with a lighter cream belly.
- **Signature item (locked):** brass explorer goggles with amber lenses. They sit up on
  the forehead by default and come down over the eyes for "looking closely" beats.
- **Flexible:** expression, pose, scale in frame, and goggles up or down.
- **Forbidden:** a realistic microscope texture, teeth, human hands or fingers, clothing
  other than the goggles, extra accessories, any text, and any brand marks.
- **Rights:** an original fictional animal mascot with no real-person likeness. It is
  distinct from the reference creator's mascot in species, silhouette and colour.

## Route A: Ink Field Notes

- **Thesis:** make discovery feel like a scientist's notebook. Cobalt ink on warm paper
  carries every idea, and Moss is the margin doodle who comes alive.
- **Principles:** one focal object per frame with generous negative space; flat with no
  perspective depth; one yellow highlight on the thing that matters.
- **Signature move:** the yellow highlight lands on the evidence (the grain, the field line).
- **Proof result:** Moss held the goggles and palette but changed body shape in the hook.
  A faint stray lettering artifact appeared at the top of the hook frame. Captions read
  well but feel quiet on cream.
- **Strength:** continuous with today's Illustrated Science default, the cheapest switch,
  and calm and credible.
- **Failure mode:** low thumbnail energy, so it reads like a textbook. The paper ground is
  close to what the old scanned-diagram gate rejected (fixed on this branch).
- **Cost:** lowest. The current prompt system already matches it.

## Route B: Pixel Night Lab (recommended)

- **Thesis:** science as a night-time adventure game. A glowing pixel Moss explores a
  dark universe where every invisible force is drawn in neon.
- **Principles:** a dark navy ground so that the subject and captions own the contrast;
  invisible things (fields, forces, flows) are always drawn as neon lines; Moss is the
  brightest shape and appears in the hook and at least every third beat.
- **Signature move:** neon force lines (magenta and amber) that literally show the
  mechanism the narration names.
- **Proof result:** the strongest continuity of the three; Moss is near-identical across
  all three frames. The hook read instantly with captions. Two defects: a faint
  checkerboard ("fake transparency") pattern in the background, and the young-Sun disk
  drawn as a spiral galaxy. Both are prompt fixes.
- **Strength:** scroll-stopping, the most recognisable at thumbnail size, and the best
  caption contrast. Pixel art forgives AI drift, and it animates cheaply (sprite bob,
  twinkling stars).
- **Failure mode:** can read as kids' content; dense biology (cells, proteins) loses
  detail at low pixel resolution.
- **Cost:** low. The flat dark ground also compresses well.

## Route C: Clay Diorama

- **Thesis:** a handmade miniature world. Moss is a clay figure, and the universe is
  built from painted rock, felt and wire on a tabletop.
- **Principles:** tactile materials over glowing effects; shallow depth of field with
  warm side light; every abstract idea becomes a physical model (wire field lines, felt nebula).
- **Signature move:** a macro tilt-shift close-up that makes a cosmic idea feel holdable.
- **Proof result:** the most premium-looking. The hook (wire field lines over a meteorite
  disc) is excellent. The explain frame drew planets already formed, which is wrong for
  the early disk, and Moss looked pasted in when floating in space.
- **Strength:** high perceived production value and a "how did they make this" factor.
- **Failure mode:** realism invites uncanny drift; space scenes break the tabletop logic;
  slower to read than B.
- **Cost:** medium. It needs the most rerolls to stay on-model.

## Fixes required for round 2, whichever route wins

1. **Caption band:** in every story frame the model placed Moss in the caption band
   (about y 1250–1600 of 1920). The prompt must pin Moss to the upper or side thirds
   and leave the lower-middle band empty, and QA must check it.
2. **Science accuracy:** "protoplanetary disk, a flat smooth ring of gas and dust, not a
   galaxy, no spiral arms, no planets yet."
3. **Background artifacts:** "solid background, no checkerboard or transparency pattern"
   (B), plus the existing no-text negatives (A).
4. **Model access:** the Gemini key is free-tier with zero image quota, so proofs ran
   through FAL's Nano Banana (`fal-ai/nano-banana`, `/edit` for reference-backed scenes).

## Round-1 spend

9 images on `fal-ai/nano-banana` at about $0.039 each, roughly $0.35.

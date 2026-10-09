---
name: hook-alignment
description: Score and rewrite a short-form video hook across its three channels - spoken line, on-screen text, and opening visual. Use when picking between hook variants, diagnosing why a video underperformed, or before publishing to TikTok. Triggers on "check the hook", "which hook", "why did this flop", "hook alignment", "score this hook".
---

# Hook Alignment

A hook is not one line. It is **three channels firing in the first three seconds**:
the spoken line, the on-screen text, and the opening visual/format. Videos with
near-identical content differ by 8x in reach depending on whether those three
channels are aligned or redundant.

## The evidence this is built on

Two videos, same creator, same system, same week:

| | Winner (89k views, 3.2x outlier) | Loser (12k views, 0.4x outlier) |
|---|---|---|
| **Spoken** | "Claude just mass-downloaded every TikTok in my niche" | "I built a system that scrapes TikTok data with Claude" |
| **Text** | "I built a TikTok spy tool with AI" | "AI TikTok Scraper" |
| **Format** | Split screen — tool visibly running | Selfie — no visual proof |

Same tool. 8x difference. Three things separate them, and all three are fixable.

## The three rules

### 1. Spoken channel: lead with the outcome, not the process

"I built X" is a **process** frame — it asks the viewer to care about your effort.
"X just did Y" is an **outcome** frame — it shows the viewer a result.

The winner said *"mass-downloaded every TikTok in my niche."* The loser said
*"I built a system that scrapes."* Identical fact, opposite framing.

Test: does the first clause name a **result that already happened**, or your
**activity**? If the sentence starts with "I built", "I made", "I created",
"here's how I", or "let me show you" — it is process. Rewrite it so the subject
is the thing, not you.

### 2. Text channel: complement the spoken line, never label it

The loser's on-screen text was *"AI TikTok Scraper"* — a caption for the spoken
line. That is a wasted channel: it tells the viewer nothing the audio didn't.

The winner's text was *"I built a TikTok spy tool with AI"* — a **different
angle** on the same fact. Audio carries the shock; text carries the framing.
Together they say more than either alone.

Test: if you deleted the audio, would the text still deliver a distinct idea?
If the text is a noun phrase summarizing the audio, it is a label — rewrite it.

### 3. Visual channel: show proof, not a person talking

Split screen with the tool visibly running beat a selfie. The visual has to
**evidence the claim in the first second**. A talking head is the absence of
evidence.

Test: in frame 1, is the thing the hook is about visible on screen? If the first
frame is a person, a title card, or an abstract graphic, the visual channel is
contributing nothing.

## Scoring a hook

Score each channel 0–2. **A hook scoring below 4 should not ship.**

| | 0 | 1 | 2 |
|---|---|---|---|
| **Spoken** | Process frame ("I built…") | Outcome, but vague | Outcome + a concrete shock word |
| **Text** | Restates or labels the audio | Different words, same idea | Genuinely different angle |
| **Visual** | Person / title card / abstract | Related but not proof | The subject, visibly doing the thing |

Report the per-channel score, the total, and — for any channel under 2 — a
concrete rewrite, not a note.

## Applying this to Clipper

The pipeline already produces all three channels; nothing currently checks them
against each other.

| Channel | Where it lives |
|---|---|
| Spoken | `hook_variants[best_hook_index]` — also scene 1 `speech` |
| Text | `cover_line` (3–5 words, ALL CAPS) |
| Visual | scene 1 `visual` + its routed lane (`photo` / `schematic` / `graphic`) |

Known gaps to check for:

- **`best_hook_index` is chosen on the spoken line alone.** A variant that scores
  well spoken may be the worst of the three once paired with `cover_line`.
  Score all variants across all three channels, then pick.
- **`cover_line` is generated independently of the hook**, so it drifts toward
  labelling. This is the most common failure — check it first.
- **Scene 1 routed to `graphic`** means the opening frame is a code-rendered card.
  That is a 0 on the visual channel. A hook scene should route to `photo` where a
  real referent exists.

## Working method

1. Extract the three channels. Quote them verbatim — do not paraphrase.
2. Score each 0–2 against the table. State the reason in one clause.
3. Rewrite every channel scoring under 2. Give the actual replacement text.
4. If comparing variants, score them all and show the table — the best spoken
   line is often not the best hook.

## What not to do

- Don't add hype to raise the spoken score. "Mass-downloaded" is shocking because
  it is **specific and true**, not because it is loud. Clipper's prompt already
  bans "you won't believe" / "this changes everything" — this skill does not
  override that.
- Don't make the text longer to make it "different". Different angle, same brevity.
- Don't recommend a talking-head format as a fix — that is the losing format.

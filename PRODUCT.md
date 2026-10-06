# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One operator: the creator who runs SCAP for a faceless science channel (TikTok
"Scientific 🧪", @60s.science2, with Instagram, YouTube and Facebook planned).
They work in a focused daily session on a laptop, aiming for 2–3 finished videos
a day.

## Product Purpose

SCAP turns science news into narrated vertical shorts in one recognisable look,
then posts them. Success is a daily batch of 2–3 videos the operator is willing
to publish, made quickly and cheaply, without per-video tinkering.

## Positioning

Every SCAP video shares one locked world: Pixel Night Lab scenes (wordless pixel
art, forces drawn as neon) hosted by Moss, an original animated tardigrade
mascot, with word-synced captions. The look is fixed, so viewers recognise the
channel and the operator never chooses styles.

## Operating Context

The daily loop: run story discovery (ranked science stories), pick stories or
paste an article URL, let SCAP summarise it into a script and scenes, generate
the video, review it in the player (does the hook land, does the ending pay
off), then post it to connected platforms. The operator keeps an eye on
provider balances (FAL, OpenRouter) in the header. SCAP runs locally at
localhost:5050 under a LaunchAgent.

## Capabilities and Constraints

- Flask backend; the dashboard is plain HTML/CSS/JS served directly, with no build step.
- Kept on the dashboard: story discovery, article list with summary, script,
  hook variants and voice-tone choice, video generation and playback, and
  posting to TikTok, Instagram, YouTube and Facebook.
- Removed by the operator's choice: visual-style choices, colour intensity, image
  sources, the AI video hook, the photo carousel, Substack posts, and send-to-phone QR.
- Posting is manual only: a finished render never uploads itself.
- TikTok posts are private-only until TikTok audits the app.
- A video costs about $0.06 in images; generation takes a few minutes.

## Brand Commitments

- The product is called SCAP. "Clipper" is the old internal name and should not
  appear in the interface.
- Moss and the Pixel Night Lab palette (deep navy, neon magenta, amber, Moss's
  mint) are the channel's approved identity for videos (docs/style-lock/STYLE.md).
  Whether the dashboard itself adopts them is an open design decision.

## Evidence on Hand

- Moss's approved reference and sprite poses: `assets/moss/`, `assets/moss/sprites/`.
- The style direction packet and proof frames: `docs/style-lock/`.
- Real rendered videos and real discovered stories in the local database.
- No testimonials, metrics or audience figures exist; none may be invented.

## Product Principles

1. The daily loop is the product: discover, generate, review, post. Everything
   else is secondary.
2. Review before publishing: the interface makes the hook and ending easy to
   judge before anything goes out.
3. Spend is always visible: balances and the cost of the next video are never
   hidden.
4. One look, no knobs: the interface never offers choices the locked style has
   already made.

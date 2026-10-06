---
name: SCAP
description: The operator's night shift. A dashboard for turning science news into Pixel Night Lab shorts hosted by Moss.
colors:
  navy-deep: "#070b1c"
  navy-ground: "#0b1026"
  navy-panel: "#10173a"
  navy-raised: "#172049"
  navy-hover: "#212c5c"
  line: "#1d2754"
  line-strong: "#2b3870"
  text: "#e9edfa"
  text-secondary: "#b3bcde"
  text-muted: "#8a94c2"
  moss-mint: "#9ed9c3"
  mint-hover: "#bdeedb"
  working-amber: "#ffb000"
  posted-magenta: "#ff3da8"
  failed-coral: "#ff7a7a"
typography:
  heading:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
  xxl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.moss-mint}"
    textColor: "{colors.navy-ground}"
    rounded: "{rounded.md}"
    height: "36px"
    padding: "0 14px"
  button-primary-hover:
    backgroundColor: "{colors.mint-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.moss-mint}"
    rounded: "{rounded.md}"
    height: "36px"
  button-post:
    backgroundColor: "{colors.posted-magenta}"
    textColor: "{colors.navy-ground}"
    rounded: "{rounded.md}"
    height: "34px"
  chip:
    backgroundColor: "{colors.navy-panel}"
    textColor: "{colors.text}"
    rounded: "{rounded.pill}"
    height: "32px"
  panel:
    backgroundColor: "{colors.navy-panel}"
    rounded: "{rounded.lg}"
  input:
    backgroundColor: "{colors.navy-ground}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    height: "36px"
---

# Design System: SCAP

## Overview

Night Shift. The operator works inside the channel's own night: the Pixel Night Lab navy is the ground, and the video palette is used only to show state. Mint means ready or the next action, amber means working, magenta means posted, and coral means failed. The world contributes type, palette, density and one signature move, Moss's run. Everything else is a quiet tool for a daily session on a laptop: discover, generate, review, post, 2–3 times a day.

The first screen leads with the discovery shortlist (left, 5 columns) beside the story list (right, 7 columns). Below 1100px they stack, discovery first.

## Colors

### Primary
- **Moss mint** `#9ed9c3`: the one primary action per area (Find today's stories, Make video), done stages, the focus ring and selection. Mint is Moss's body colour, so the next thing to do is the mascot's colour.

### Secondary
- **Working amber** `#ffb000`: anything in progress (stage strip, run slot, pending platform posts). Only shown while something is happening.
- **Posted magenta** `#ff3da8`: published and the Post action. Magenta is the neon of the videos, so it marks what has left the building.
- **Failed coral** `#ff7a7a`: failure states and Delete.

### Neutral
- Navy ramp: deep `#070b1c` (top bar, run band), ground `#0b1026` (page, expanded story), panel `#10173a`, raised `#172049`, hover `#212c5c`.
- Lines `#1d2754` and `#2b3870` carry structure instead of shadows.
- Text is tinted from the navy: `#e9edfa`, `#b3bcde`, `#8a94c2`. All three clear 4.5:1 on every navy surface (the lowest is muted on raised at 5.3:1).

### Named Rules
- **State, not decoration.** A state colour appears only when the thing it labels is in that state. Never use mint, amber or magenta as an accent.
- **One mint per area.** Each panel has at most one filled mint button; repeated row actions are outlined.

## Typography

The system sans carries everything an operator reads. The only display voice is the authored pixel SCAP wordmark (inline SVG, 3px cells), which echoes the videos without making the tool harder to read. Numbers (scores, balances, counts, character counters) use tabular numerals.

### Hierarchy
- Panel heading: 1.25rem / 650.
- Story and candidate title: 0.9375rem / 600, clamped to two lines when collapsed.
- Body and summaries: 0.9375rem / 400, measure capped at 72ch.
- Labels, meta and stages: 0.75–0.8125rem / 600.

## Layout

A sticky 56px top bar, then the run band, then a two-column workspace (5fr / 7fr, 24px gap) inside a 1440px max-width with 24px gutters (16px under 760px). Lists are rows separated by 1px lines inside a panel, not stacks of cards. Story rows keep a title column, a stage strip and the status/actions cluster on one line. Below 760px the stage strip drops to its own row.

## Elevation & Depth

The surface is flat, with depth carried by the navy ramp and lines. Only floating layers cast a shadow: popovers, dialogs and toasts use `0 14px 36px rgba(2,4,14,.55), 0 2px 6px rgba(2,4,14,.4)`. Overlays dim the page to `rgba(3,5,16,.76)`, with no blur.

## Shapes

Radii are 6px (stages, chips inside rows), 8px (buttons, inputs, inner blocks), 12px (panels, dialogs) and a full pill for header chips. Dots are 8px circles; stage markers are 7px squares with 2px radius, a quiet nod to the pixel grid.

## Components

### Buttons
Primary is filled mint with navy text. Secondary is a mint outline. Post is filled magenta. Danger is coral text on transparent. Each button pairs a drawn 14px stroke icon from the inline sprite with a verb label. Heights are 34–36px.

### Chips
The header chips (Balances, Accounts) are pills with a status dot and a chevron; they open popovers anchored to the right.

### Cards / Containers
There are two panels (Find stories, Stories). Inside them, everything is a row. The expanded story sits on the darker ground and shows the player, the Post and Download actions, the review question, the summary, a script disclosure, hashtags and the actions bar.

### Inputs / Fields
Navy ground, 1px strong line, 8px radius. Focus shows a mint border with a 3px mint-tint ring. Selects use a drawn chevron. Checkboxes are accented in mint.

### Navigation
There is no navigation. The product is one screen.

### Moss's run (signature)
Today's slots are `DAILY_GOAL = 3`, growing to at most five when more stories are added today. Each slot shows its state (open, script ready, working, ready to review, posted, failed). Moss, the channel's tardigrade sprite at 46px, stands on the slot that needs attention and moves between slots in 8 pixel steps (`steps(8)`), snapped to a 4px grid:
- walking (a two-frame cycle) while something is working;
- waving at an open slot;
- thinking at a failure;
- delighted when the day's goal is met;
- blinking when idle.

Under reduced motion he jumps without travelling. This is the only authored motion on the page.

### Stage strip
Script → Video → Review → Post, with each step marked as done, working, ready, posted or failed. It replaces the old status badges, and Review turns filled mint when a video is waiting for the operator.

## Do's and Don'ts

### Do:
- Use the drawn icon sprite (`icon(name)` in app.js) for every icon.
- Theme browser surfaces: selection, caret, scrollbars, focus ring and underline offset.
- Keep user-facing errors generic and name the recovery.
- Call the product SCAP.

### Don't:
- Use eyebrows or kickers, gradient text, glass, or unicode/emoji glyphs as icons.
- Offer style, colour or image-source choices. The look is locked.
- Use a modal unless the task needs it. Only the player and the Post dialog are modal.
- Animate entrances beyond a 6px, 240ms settle.

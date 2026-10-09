# Surface brief: dashboard (static/index.html)

Scope: the single-page SCAP dashboard. Mode: Operate. Primary target: static/index.html,
with static/app.js and static/styles.css.

Audience and job: one operator in a daily laptop session, turning 2–3 science stories a
day into posted videos. They lead with the discovery shortlist. A polished result feels
wrong if it is too dense or adds clicks between finding a story and posting it.

## Direction contract

THESIS: The dashboard lives in the channel's own night: the operator works inside the
Pixel Night Lab world, not in a generic dark SaaS shell. It refuses the category default
of glass cards, purple gradients and badge pills stacked in one long column.

OWN-WORLD: Deep navy ground (#0B1026) with a darker shell band; Moss's mint means ready,
amber means working, magenta means posted or live, and coral means failed. Workhorse
system sans for everything readable, a hand-authored pixel wordmark for SCAP, tabular
numerals, hairline navy rules, pixel-crisp Moss sprites. No gradients, glass or glow.

STORY: The operator sees today's progress at a glance, picks a ranked story, watches it
move through Script, Video, Review and Post, reviews it, and posts it.

FIRST VIEWPORT: The top bar holds the SCAP wordmark, balances and accounts. Under it,
"Moss's run" is a slim track with today's three slots, and Moss stands on the active
one. Below that are two columns: Find stories on the left (lead; ranked rows with a
score and a Make video action, and paste-URL at the foot), and Stories on the right
(search, then rows with a four-stage strip).

FORM: Night Shift, chosen by the user from three manual candidates (Night Shift, Broadcast
Rundown, Clean standard tool). Seed key: none, because concept-seed did not run (the
binary was not executed). Raise from Broadcast Rundown: stories read as rows with stage
columns, kept airy because the operator rejected density. Signature move: Moss's run.
Build path: code-led.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

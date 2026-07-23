# Clipper — Article to TikTok Video Generator

Turn any article URL into a TikTok-ready video with AI-generated images, voiceover, and a viral script.

## How It Works

1. **Paste a URL** (via Discord or the web dashboard)
2. **AI summarizes** the article into a punchy 60-second script
3. **AI generates images** (FAL.ai) or fetches stock photos (Pexels)
4. **Pluggable TTS** creates a stable Gemini or local Kokoro/Qwen3 voiceover
5. **faster-whisper** aligns each spoken word for burned-in captions
6. **MoviePy** assembles everything into a vertical video
7. **Video is posted** to your Discord output channel

## Voice Engines

`TTS_ENGINE=auto` is the default: Clipper tries Gemini once, retries once on
failure, then falls back to the fixed Kokoro voice. A named cloud/local engine
also falls back to Kokoro, which is always the terminal engine. No engine picks
a random voice.

| Engine | Default voice | Typical CPU/network speed | Cost |
|--------|---------------|---------------------------|------|
| Gemini 3.1 Flash TTS Preview | `Puck` (upbeat) | Network request; usually seconds | Free tier available; paid audio is about $0.03/minute |
| Kokoro-82M | `af_heart` | Local CPU; generally near real-time | Free/local |
| Qwen3-TTS 0.6B CustomVoice | `Ryan` (dynamic English) | Local CPU; roughly 3–5× audio duration | Free/local; larger model download and RAM use |

Gemini uses `gemini-3.1-flash-tts-preview`, the newest Flash TTS model in
Google's [speech generation guide](https://ai.google.dev/gemini-api/docs/speech-generation).
Pricing is from the [Gemini API pricing page](https://ai.google.dev/gemini-api/docs/pricing).

Configure the channel's stable voice in `.env`:

```env
TTS_ENGINE=auto
GEMINI_TTS_VOICE=Puck
KOKORO_VOICE=af_heart
QWEN3_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice
```

Qwen3 is intentionally optional because its dependency stack and model are
substantially larger. The official package recommends a dedicated environment;
to enable it in the environment running Clipper:

```bash
pip install -U qwen-tts
```

Then set `TTS_ENGINE=qwen3`. Clipper loads Qwen lazily on CPU and uses its
native English `Ryan` voice. The default model is the smallest preset-voice
checkpoint; `QWEN3_TTS_MODEL` overrides it with another CustomVoice checkpoint.

To compare every available engine and all bundled Kokoro voices by ear:

```bash
python tts_preview.py
```

Previews are written to `static/tts_previews/`. In Discord, run `!voices` to
render the same samples and post WAV files small enough for mobile playback.

## Word-Synced Captions

Captions are enabled by default. The CPU-only `tiny` faster-whisper model
produces word timestamps, which Clipper groups into 2–4-word cues and renders
with Pillow. The video also shows the article headline for the first 2.5
seconds. This does not require ImageMagick.

Montserrat is bundled in `static/fonts/` under the SIL Open Font License. The
first captioned run downloads the selected Whisper model and caches it locally.

Optional `.env` tuning:

```env
# tiny is fastest on Oracle ARM; base trades speed for accuracy
WHISPER_MODEL=tiny
WHISPER_CPU_THREADS=2
WHISPER_DOWNLOAD_TIMEOUT=60
CAPTION_UPPERCASE=true
VIDEO_CRF=26
```

`CAPTION_UPPERCASE=true` uppercases every cue consistently. Cue grouping keeps
numbers with their units, avoids ending on articles/prepositions, and removes
trailing sentence punctuation consistently. Each currently spoken word is
highlighted karaoke-style. Set the flag to `false` to preserve Whisper's
letter case. `VIDEO_CRF=26` controls H.264 quality and file size; lower values
increase both.

Code that needs a clean render without word captions can call
`generate_video(..., captions=False)`. The opening headline remains enabled.

## Discord Bot (3-Channel Workflow)

| Channel | Purpose |
|---------|---------|
| `#input` | Paste article URLs here |
| `#processing` | Bot posts progress updates |
| `#output` | Final video is delivered here |

**Commands:** `!generate <url>`, `!discover [top_n]`, `!status`, `!stats`, `!voices`

## Automated Story Discovery

Clipper collects current science headlines from ScienceDaily, Phys.org, Live
Science, EurekAlert, and Reddit's r/science top-of-day feed. It removes URLs
already stored in the Article table, then sends the complete unseen batch to
Groq once for 0–100 viral scoring based on wow-factor, visual potential, broad
appeal, curiosity gap, and fit for a 60-second @60s.science2 TikTok.

Manual discovery is always available from the dashboard's **Find today's
stories** button or Discord:

```text
!discover
!discover 3
```

The bot posts the scored shortlist in the processing channel, then sends the
highest-ranked unseen stories through the normal scrape → summarize → video →
output flow. `top_n` is capped at 5 for a manual command.

Daily discovery is opt-in:

```env
DISCOVERY_ENABLED=false
DISCOVERY_HOUR_UTC=9
DISCOVERY_TOP_N=2
```

Set `DISCOVERY_ENABLED=true` to run once daily at the configured UTC hour.
Reconnects do not start duplicate scheduler tasks. A `GROQ_API_KEY` is required
for discovery scoring.

## Scene-Based Videos, Visual Styles & AI Video Hook

When the summarizer returns scene beats (`scenes`, `dominant_emotion`,
`suggested_style`), each narration beat gets its own style-consistent image
(presets in `visual_styles.py`, picker in the dashboard), and the dominant
emotion steers the voice: Kokoro picks a matching voice + speaking speed,
Gemini/Qwen3 get a matching delivery instruction. Articles summarized before
this feature fall back to the legacy keyword-image path automatically.

The 5-second opening hook can optionally be a real AI video clip from FAL
instead of stills — toggle it per-run in the dashboard, or set a default:

```env
# HOOK_VIDEO_MODEL=fal-ai/ltx-video
# HOOK_VIDEO_ASPECT=9:16
```

Any failure of the video hook silently falls back to the image hook.

When real motion is enabled, Clipper can also animate selected high-impact body
scenes. `MAX_VIDEO_CLIPS_PER_VIDEO=3` caps the hook and body clips together;
the estimated motion cost is logged before rendering and any failed clip falls
back to its existing still/Ken Burns scene.

## Music Bed

Three CC0 procedural loops are bundled in `static/audio/music/`. With
`MUSIC_ENABLED=true` (the default), one is mixed beneath narration at about
-22 dBFS during speech and -12 dBFS in gaps, using the existing Whisper word
timings for ducking. Music fades in for 0.5 seconds and out over the final
second. Missing or invalid audio always falls back to voice-only rendering.

## Substack Companion Posts

`POST /api/articles/<id>/substack` (or the dashboard button) generates a
long-form, conversational Substack post from the article's summary and scene
beats. Cached on the article; pass `{"regenerate": true}` to rewrite.

## TikTok Direct Post

Clipper can connect one creator account with TikTok Login Kit and upload a
generated MP4 through the Content Posting API. OAuth access and refresh tokens
are encrypted before they are stored in SQLite, refreshed automatically, and
never returned by the API.

Configure the TikTok app and callback:

```env
FLASK_SECRET_KEY=a-long-random-secret
TIKTOK_CLIENT_KEY=your-client-key
TIKTOK_CLIENT_SECRET=your-client-secret
TIKTOK_REDIRECT_URI=https://clipper.example.com/api/tiktok/oauth/callback
TIKTOK_TOKEN_ENCRYPTION_KEY=a-separate-long-random-secret
TIKTOK_ALLOW_PUBLIC_POSTS=false
SESSION_COOKIE_SECURE=true
```

In the TikTok Developer Portal, add Login Kit and Content Posting API, register
the exact static HTTPS redirect URI above, and enable `user.info.basic` plus
`video.publish`. Those two scopes are sufficient to connect and post.

Performance feedback is optional. After adding Display API and enabling both
`video.list` and `user.info.stats` for this exact TikTok app, set
`TIKTOK_REQUEST_METRICS_SCOPES=true` and click **Reconnect** once. Leave that
flag false until the scopes are available: TikTok rejects the entire Login Kit
request when it contains an unavailable scope. Clipper refreshes
view/like/comment/share counters on a schedule and feeds top/bottom performers
back into story scoring. TikTok's Display API does not expose watch time, so
that field remains empty rather than being estimated.

Publishing is manual-only. Generating a video never starts an upload: review
the finished video, choose **Post**, select the destination(s), and confirm.
Until TikTok audits the client, Clipper enforces `SELF_ONLY` (Only you). TikTok
also requires the connected TikTok account itself to be **Private** while the
client is unaudited.

## Manual multi-platform publishing

Finished videos use one **Post** picker. Each connected destination is opt-in
for that video, and each result is stored independently. One destination
failing does not prevent the others from completing.

Instagram Reels uses Meta's pull-based Content Publishing API. Clipper creates a
short-lived HMAC-signed URL under `/public-media/`; the supplied `Caddyfile`
allows only that signed route through without Basic Auth. Everything else stays
protected. Configure the Oracle/Caddy HTTPS origin and Meta callback:

```env
PUBLIC_BASE_URL=https://clipper.example.com
PUBLIC_MEDIA_SIGNING_KEY=a-long-random-secret
INSTAGRAM_APP_ID=your-meta-app-id
INSTAGRAM_APP_SECRET=your-meta-app-secret
INSTAGRAM_REDIRECT_URI=https://clipper.example.com/api/instagram/oauth/callback
```

The Instagram account must be Business or Creator and linked to a Facebook
Page. The Meta app needs `instagram_basic` and `instagram_content_publish`, plus
App Review before posting for users outside the app's test roles. Meta limits
the account to 50 published posts per rolling 24 hours. Long-lived tokens last
roughly 60 days; Clipper refreshes them near expiry and displays an expiry
warning in the header.

Facebook Reels publishes to a managed Facebook Page with Meta's
start/upload/finish flow:

```env
FACEBOOK_APP_ID=your-facebook-app-id
FACEBOOK_APP_SECRET=your-facebook-app-secret
FACEBOOK_REDIRECT_URI=https://clipper.example.com/api/facebook/oauth/callback
FACEBOOK_GRAPH_VERSION=v23.0
```

The Page connection needs `pages_show_list`, `pages_manage_posts`, and
`pages_read_engagement`. App Review is required before people outside the Meta
app's roles can publish. Clipper stores the Page access token with the existing
encrypted `PublisherAccount` store, polls processing Reels to a terminal state,
and leaves each other selected destination independent if Facebook fails.

YouTube Shorts uses `videos.insert` with a resumable upload and the
`youtube.upload` scope:

```env
YOUTUBE_CLIENT_ID=your-google-oauth-client-id
YOUTUBE_CLIENT_SECRET=your-google-oauth-client-secret
YOUTUBE_REDIRECT_URI=https://clipper.example.com/api/youtube/oauth/callback
```

Publish the Google OAuth consent screen: refresh tokens from projects left in
Testing expire after seven days. Unverified upload projects are restricted to
private videos until Google completes verification. YouTube's current default
granular Video Uploads quota is 100 uploads per day.

The intended operational defaults are explicit in `.env.example`:

```env
DISCOVERY_ENABLED=true
MUSIC_ENABLED=true
MAX_VIDEO_CLIPS_PER_VIDEO=3
TIKTOK_ALLOW_PUBLIC_POSTS=false
TIKTOK_REQUEST_METRICS_SCOPES=false
```

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API keys

# 2. Run with Docker
docker compose up -d

# 3. Or run locally (dashboard and scheduler)
pip install -r requirements.txt
python app.py
```

## Manual Caption Test

From the repository root with FFmpeg installed:

```bash
source .venv/bin/activate
pip install -r requirements.txt
python - <<'PY'
from video_generator import generate_video

path = generate_video(
    article_id=999,
    title="Scientists discover a surprising way plants communicate",
    script=(
        "[HOOK] Plants may be talking right under our noses. "
        "[BIG IDEA] Researchers found chemical signals moving between nearby plants. "
        "[CLOSE] Here is why that could change how we grow food."
    ),
    image_source="stock",
)
print(path)
PY
```

With no Pexels key, gradient backgrounds are used. The resulting MP4 in
`static/videos/` should have a headline for roughly 2.5 seconds and large,
word-synced white captions with black outlines in the safe lower third.

To list the legacy pre-caption videos, then safely re-render them only after
reviewing the dry run:

```bash
python scripts/rerender_stale_videos.py
python scripts/rerender_stale_videos.py --execute
```

The script keeps the database unchanged unless a complete replacement exists
and tries progressively smaller encodes when needed to stay under Discord's
25 MB upload limit.

## Deploy to Oracle Cloud Free Tier

```bash
./scripts/deploy.sh ~/.ssh/oracle-key.pem YOUR_SERVER_IP
```

## Required API Keys

- **GROQ_API_KEY** or **OPENROUTER_API_KEY** — for AI summarization (free tiers available)
- **FAL_KEY** — for AI image generation
- **DISCORD_BOT_TOKEN** — from Discord Developer Portal

## Optional Keys

- **PEXELS_API_KEY** — stock photo alternative to AI images
- **MISTRAL_API_KEY** — fallback summarizer
- **GEMINI_API_KEY** — Gemini summarizer fallback and primary TTS in `auto` mode; Kokoro is used without it

## Generation budget indicator

The header's **API budget** pill uses the ordinary `FAL_KEY` and
`OPENROUTER_API_KEY` to show both providers' real remaining balances. It also
shows the smaller limiting balance, an estimate of standard videos remaining,
and amber/red warnings controlled by:

```env
BUDGET_LOW_USD=5
BUDGET_CRITICAL_USD=1
```

Optional admin/management keys remain supported as overrides, but they are not
required. Failed or changed provider responses display “unavailable” rather
than a misleading zero, and no credential fragments or raw provider bodies are
returned to the browser. Groq and Gemini expose authoritative spend through
their own dashboards, so Clipper labels them as configured and links out.

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

`CAPTION_UPPERCASE=true` uppercases caption cues and removes only trailing
commas/periods (question marks and exclamation points are kept). Set it to
`false` to preserve Whisper's letter case. `VIDEO_CRF=26` controls H.264
quality and file size; lower values increase both.

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

Manual discovery is always available in Discord:

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

## Quick Start

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API keys

# 2. Run with Docker
docker compose up -d

# 3. Or run locally
pip install -r requirements.txt
python discord_bot.py
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

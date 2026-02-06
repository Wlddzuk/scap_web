# Article to TikTok Video Generator

Turn any article into a TikTok-ready short-form video with AI-generated visuals and voiceover.

## What It Does

1. **Scrape** - Paste any article URL to extract title, content, and images
2. **Summarize** - AI generates a TL;DR, key bullets, hashtags, and a viral video script
3. **Generate Video** - Creates a 9:16 vertical video with:
   - AI-generated B-roll images (FLUX.1-dev via FAL.ai)
   - High-quality TTS voiceover (Kokoro-82M)
   - Ken Burns zoom effects on images
   - Fast-paced visual changes (every 2-3 seconds)

## Current Status

| Feature | Status |
|---------|--------|
| URL Scraping | Working |
| Browser Bookmarklet | Working |
| AI Summarization (multi-provider) | Working |
| Video Script Generation | Working |
| Hashtag Generation | Working |
| AI Image Generation (FAL.ai) | Working |
| TTS Voiceover (Kokoro/gTTS) | Working |
| Video Assembly (MoviePy) | Working |
| Web Dashboard | Working |

## Quick Start

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

Dashboard: `http://localhost:5050`

## Required Setup

### 1. System Dependencies

**FFmpeg** (required for video encoding):

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt-get install ffmpeg
```

### 2. Environment Variables

Create a `.env` file:

```env
# AI Summarization - need at least ONE (OpenRouter recommended)
OPENROUTER_API_KEY=your_key    # https://openrouter.ai/keys
GROQ_API_KEY=your_key          # https://console.groq.com/keys
MISTRAL_API_KEY=your_key       # https://console.mistral.ai/api-keys/
GEMINI_API_KEY=your_key        # https://aistudio.google.com/app/apikey

# AI Image Generation (optional - uses gradients without it)
FAL_KEY=your_key               # https://fal.ai/dashboard/keys
```

**Note:** Without `FAL_KEY`, videos will use gradient backgrounds instead of AI-generated images.

## How It Works

### Video Generation Pipeline

```
Article URL
    ↓
Scrape content & hero image
    ↓
AI Summarization (OpenRouter → Groq → Mistral → Gemini fallback)
    ├── TL;DR summary
    ├── Key bullet points
    ├── Video script (hook + body)
    └── 5 trending hashtags
    ↓
Video Generation
    ├── Extract story subjects (AI)
    ├── Select visual style (3D CGI / Photography / Flat Design)
    ├── Generate image prompts (story-aware)
    ├── Create images (FAL.ai FLUX.1-dev)
    ├── Generate voiceover (Kokoro TTS → gTTS fallback)
    └── Assemble video (MoviePy)
    ↓
Output: 1080x1920 MP4 @ 30fps
```

### AI Provider Fallback Chain

The app automatically tries providers in order until one succeeds:

1. **OpenRouter** - Access to many models, recommended
2. **Groq** - Fast inference, generous free tier
3. **Mistral** - High-quality alternative
4. **Gemini** - Final fallback

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/articles` | List all articles |
| GET | `/api/articles/<id>` | Get single article |
| POST | `/api/scrape-url` | Scrape article from URL |
| POST | `/api/scrape` | Submit from bookmarklet |
| POST | `/api/articles/<id>/summarize` | Generate AI summary |
| POST | `/api/articles/<id>/video` | Generate video |
| DELETE | `/api/articles/<id>` | Delete article |
| GET | `/api/health` | Health check |

## Project Structure

```
scap_web/
├── app.py              # Flask backend (routes, API)
├── models.py           # SQLAlchemy database models
├── summarizer.py       # AI summarization (multi-provider)
├── video_generator.py  # Video creation pipeline
├── static/
│   ├── index.html      # Dashboard UI
│   ├── app.js          # Frontend logic
│   ├── styles.css      # Styling
│   └── videos/         # Generated videos
├── instance/           # SQLite database (auto-created)
└── requirements.txt
```

## Tech Stack

- **Backend:** Python 3.11, Flask
- **Database:** SQLite
- **AI Summarization:** OpenRouter, Groq, Mistral, Gemini
- **Image Generation:** FAL.ai (FLUX.1-dev)
- **TTS:** Kokoro-82M, gTTS (fallback)
- **Video:** MoviePy, Pillow
- **Frontend:** Vanilla JS, CSS3

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

50 tests covering security, API integration, and video generation.

## Production Deployment

```bash
# Docker
docker-compose up -d

# Or with Gunicorn
gunicorn -c gunicorn.conf.py wsgi:app
```

See [DEPLOYMENT.md](DEPLOYMENT.md) for full deployment guide.

## License

MIT

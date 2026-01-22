# External Integrations

**Analysis Date:** 2026-01-22

## APIs & External Services

**AI Summarization (Multi-Provider Fallback Chain):**
- **OpenRouter** - Primary LLM summarization provider
  - SDK/Client: `requests` library (direct HTTP)
  - Model: `meta-llama/llama-3.3-70b-instruct`
  - Auth: `OPENROUTER_API_KEY`
  - Endpoint: `https://openrouter.ai/api/v1/chat/completions`
  - Implementation: `summarizer.py:summarize_with_openrouter()`

- **Groq** - Secondary LLM provider
  - SDK/Client: `groq` package
  - Model: `llama-3.3-70b-versatile`
  - Auth: `GROQ_API_KEY`
  - Implementation: `summarizer.py:summarize_with_groq()`
  - Also used for: style selection, story subject extraction, image prompt generation (see `video_generator.py`)

- **Mistral** - Tertiary LLM provider
  - SDK/Client: `requests` library (direct HTTP)
  - Model: `mistral-large-latest`
  - Auth: `MISTRAL_API_KEY`
  - Endpoint: `https://api.mistral.ai/v1/chat/completions`
  - Implementation: `summarizer.py:summarize_with_mistral()`

- **Google Gemini** - Final fallback LLM
  - SDK/Client: `google-generativeai` package
  - Model: `gemini-2.0-flash`
  - Auth: `GEMINI_API_KEY`
  - Implementation: `summarizer.py:summarize_with_gemini()`

**Image Generation:**
- **FAL.ai** - AI image generation for video B-roll
  - SDK/Client: `fal_client` package (dynamically imported)
  - Model: `fal-ai/flux/schnell` (FLUX.1 fast variant)
  - Auth: `FAL_KEY` env var (optional; fallback to gradient backgrounds if missing)
  - Configuration:
    - Image size: `portrait_16_9` (9:16 vertical)
    - Inference steps: 4 (fast generation)
    - Output format: PNG via HTTPS URL download
  - Implementation: `video_generator.py:generate_image_fal()`
  - Fallback: Gradient background generation if FAL_KEY missing or API fails

**Text-to-Speech:**
- **Kokoro-82M** - Primary TTS provider
  - SDK/Client: `kokoro` package (dynamically imported)
  - Model: `hexgrad/Kokoro-82M` from HuggingFace
  - Language: English (lang_code="a")
  - Voice: `af_heart` (female voice)
  - Speed: 1.05x
  - Output format: WAV (24kHz)
  - Implementation: `video_generator.py:generate_tts_kokoro()`

- **Google Text-to-Speech (gTTS)** - TTS fallback
  - SDK/Client: `gTTS` package
  - Language: English
  - Output format: MP3
  - Implementation: `video_generator.py:generate_tts_kokoro()` fallback clause

## Data Storage

**Databases:**
- **SQLite** - Local relational database
  - Type: SQLite3
  - Connection: `sqlite:///instance/database.db` (default local file)
  - Client/ORM: Flask-SQLAlchemy
  - Schema: `models.py` contains `Article` model with:
    - Article metadata (url, title, content, hero_image, site_name)
    - Status tracking (scraped, summarizing, summarized, generating_video, video_done, failed)
    - Summary fields (tldr, bullets, video_script, hashtags)
    - Video output path
    - Timestamps (scraped_at, summarized_at, video_generated_at)

**File Storage:**
- **Local Filesystem Only** - No external storage
  - Generated videos: `static/videos/` directory (mounted volume in Docker)
  - Database: `instance/database.db`
  - Static assets: `static/` (HTML, CSS, JS, images)

**Caching:**
- None detected

## Authentication & Identity

**Auth Provider:**
- Custom - No authentication system
- API keys are environment-variable based
- No user authentication or authorization implemented
- CORS configured for specific origins (configurable via `CORS_ORIGINS`)

**API Key Management:**
- Environment variables (`.env` file)
- Docker environment variable passing via `docker-compose.yml`
- Validation on startup: `app.py:validate_api_keys()`
- At least one summarization API key required; others are optional

## Monitoring & Observability

**Error Tracking:**
- None detected

**Logs:**
- Standard Python logging to stdout
- Flask access logs via gunicorn to stdout
- Log format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`
- Verbose stdout messages for video generation pipeline steps

**Health Check:**
- HTTP GET `/api/health` endpoint returns JSON status and timestamp
- Docker compose healthcheck: Makes HTTP request to health endpoint every 30s, 3 retries, 10s timeout

## CI/CD & Deployment

**Hosting:**
- Docker containerization via `Dockerfile` (not shown in reads, but referenced)
- Docker Compose for local/cloud deployment
- Gunicorn WSGI server for production

**CI Pipeline:**
- None detected (no GitHub Actions, GitLab CI, etc.)

**Deployment Methods:**
- Docker Compose (primary): `docker-compose up -d`
- Gunicorn directly: `gunicorn -c gunicorn.conf.py wsgi:app`
- Development: `python app.py` (Flask debug mode)

## Environment Configuration

**Required env vars:**
- At least ONE of: `OPENROUTER_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `GEMINI_API_KEY`
- Optional: `FAL_KEY` (image generation; uses gradients if missing)
- Optional: `HUGGINGFACE_API_KEY` (present but unused)
- Optional: `CORS_ORIGINS` (defaults provided)
- Optional: `DATABASE_URI` (defaults to local SQLite)

**Secrets location:**
- `.env` file at project root (committed in current state; should be in `.gitignore` for production)
- Docker: Passed via `docker-compose.yml` environment section

## Webhooks & Callbacks

**Incoming:**
- None detected

**Outgoing:**
- None detected

## External Data Sources

**Web Scraping:**
- Article scraping via `requests` + `BeautifulSoup4`
- HTTP requests to arbitrary URLs (no whitelist)
- User-Agent header spoofing to bypass restrictions
- HTML parsing for title, content, hero image, site metadata
- Timeout: 15 seconds

**Hero Images:**
- Fetched from scraped article og:image meta tag
- Also downloaded from FAL.ai image generation HTTPS URLs

## Third-Party SDKs & Libraries

**HTTP/Networking:**
- `requests` - HTTP client for API calls and web scraping

**Web Frameworks:**
- Flask ecosystem - Web framework, CORS, SQLAlchemy ORM

**Data Processing:**
- `beautifulsoup4` - HTML parsing
- `Pillow` - Image processing and resizing
- `moviepy` - Video assembly
- `numpy` - Numerical operations for video generation

**AI/ML:**
- `groq` - Groq API client
- `google-generativeai` - Gemini API client
- `fal_client` - FAL.ai API client
- `kokoro` - Kokoro TTS model

**Audio/Media:**
- `gTTS` - Google Text-to-Speech
- `soundfile` - WAV audio writing

---

*Integration audit: 2026-01-22*

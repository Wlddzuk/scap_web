# Technology Stack

**Analysis Date:** 2026-01-22

## Languages

**Primary:**
- Python 3.11 - Backend API, video generation, AI integration
- JavaScript (Vanilla) - Frontend dashboard UI
- HTML5 - Dashboard markup
- CSS3 - Styling

**Secondary:**
- Shell - Docker scripts, setup scripts

## Runtime

**Environment:**
- Python 3.11 (specified in project)

**Package Manager:**
- pip - Python package management
- Lockfile: Not detected (requirements.txt specifies versions)

## Frameworks

**Core:**
- Flask 3.0.0 - Web framework for API and static file serving
- Flask-CORS 4.0.0 - Cross-origin resource sharing
- Flask-SQLAlchemy 3.1.1 - ORM and database management

**Testing:**
- pytest 7.4.0+ - Test runner
- pytest-cov 4.1.0+ - Code coverage
- pytest-flask 1.2.0+ - Flask test fixtures
- pytest-mock 3.11.1+ - Mocking framework

**Build/Dev:**
- gunicorn 21.2.0+ - Production WSGI server
- black 23.7.0+ - Code formatter
- flake8 6.1.0+ - Linting
- pylint 3.0.0+ - Advanced linting
- mypy 1.5.0+ - Static type checking
- bandit 1.7.5+ - Security linting

## Key Dependencies

**Critical:**
- moviepy 1.0.3 - Video assembly and editing, required for video output
- Pillow 10.4.0 - Image processing with Pillow 10 fix (ANTIALIAS → LANCZOS compatibility)
- beautifulsoup4 4.12.0+ - HTML parsing for article scraping
- requests 2.31.0+ - HTTP requests for APIs and content fetching

**AI & LLM Providers:**
- google-generativeai 0.8.3 - Google Gemini API client (fallback summarization)
- groq 0.4.0+ - Groq API client (primary LLM, style/subject extraction, prompt generation)
- openrouter (via requests) - OpenRouter API client (primary summarization provider)
- mistral (via requests) - Mistral API client (summarization fallback)

**Text-to-Speech:**
- gTTS 2.5.3 - Google Text-to-Speech (fallback TTS)
- kokoro (optional) - Kokoro-82M voice model (primary TTS, imported dynamically)
- soundfile (optional) - WAV audio writing for Kokoro output

**Environment & Config:**
- python-dotenv 1.0.0 - .env file loading for configuration

**Image Generation:**
- fal_client (optional) - FAL.ai client for FLUX.1-dev image generation (imported dynamically)

## Configuration

**Environment:**
Environment variables (via `.env` file, see `.env` read):
- `OPENROUTER_API_KEY` - OpenRouter API authentication (primary summarization)
- `GROQ_API_KEY` - Groq API authentication (LLM, style/subject extraction)
- `MISTRAL_API_KEY` - Mistral API authentication (summarization fallback)
- `GEMINI_API_KEY` - Google Gemini API authentication (final summarization fallback)
- `FAL_KEY` - FAL.ai API authentication for image generation (optional; gradients used if missing)
- `HUGGINGFACE_API_KEY` - Present but not actively used in current codebase
- `CORS_ORIGINS` - Comma-separated allowed origins (default: `http://localhost:3000,http://localhost:5050`)
- `DATABASE_URI` - SQLite connection string (default: `sqlite:///instance/database.db`)
- `FLASK_ENV` - Set to `production` in Docker

**Build:**
- `Dockerfile` - Container build configuration
- `docker-compose.yml` - Multi-container orchestration (at `docker-compose.yml`)
- `gunicorn.conf.py` - Production WSGI configuration at `gunicorn.conf.py`
  - Binds to `0.0.0.0:5050`
  - Workers: `cpu_count * 2 + 1` (auto-scaling)
  - Timeout: 120s (for long-running video generation)
- `nixpacks.toml` - Nix-based deployment configuration
- `apt.txt` - System dependencies (ffmpeg, imagemagick, espeak-ng)

## Platform Requirements

**Development:**
- macOS or Linux (ffmpeg, system build tools)
- Python 3.11+
- FFmpeg (required for video encoding)
- ImageMagick (optional, fallback image operations)
- espeak-ng (text-to-speech synthesis, optional)

**Production:**
- Docker/Docker Compose (recommended)
- Or: Linux host with Python 3.11, FFmpeg, system dependencies
- Gunicorn WSGI server
- Storage for generated videos (persistent volume or mounted directory)
- Outbound HTTP access for API calls (OpenRouter, Groq, Mistral, Gemini, FAL.ai)

**System Dependencies (apt.txt):**
- `ffmpeg` - Video encoding and processing
- `imagemagick` - Image manipulation fallback
- `espeak-ng` - TTS synthesis alternative

---

*Stack analysis: 2026-01-22*

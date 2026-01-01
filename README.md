# Article Scraper MVP

A production-ready web application that scrapes articles, generates AI summaries, and creates TikTok-style short-form videos with AI-generated B-roll and high-quality TTS.

[![Tests](https://img.shields.io/badge/tests-50%20passing-brightgreen)](TESTING_SUMMARY.md)
[![Security](https://img.shields.io/badge/security-hardened-blue)](SECURITY_IMPROVEMENTS.md)
[![Deployment](https://img.shields.io/badge/deployment-ready-success)](DEPLOYMENT.md)

## Quick Start

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the app
python app.py
```

The dashboard will be available at `http://localhost:5050`

## Environment Variables

Create a `.env` file in the project root:

```env
# AI Summarization (uses fallback chain: OpenRouter → Groq → Mistral → Gemini)
OPENROUTER_API_KEY=your_openrouter_api_key_here  # Get from https://openrouter.ai/keys
GROQ_API_KEY=your_groq_api_key_here              # Get from https://console.groq.com/keys
MISTRAL_API_KEY=your_mistral_api_key_here        # Get from https://console.mistral.ai/api-keys/
GEMINI_API_KEY=your_gemini_api_key_here          # Get from https://aistudio.google.com/app/apikey

# AI Image Generation for Videos (Optional - falls back to gradients)
FAL_KEY=your_fal_key_here  # Get from https://fal.ai/dashboard/keys

# Production Configuration (Optional)
CORS_ORIGINS=http://localhost:3000,http://localhost:5050,https://yourdomain.com
DATABASE_URI=sqlite:///path/to/database.db
```

### Getting Your API Keys

**FAL.ai** (Optional - for AI-generated B-roll images):
1. Create account at [FAL.ai](https://fal.ai)
2. Go to [Dashboard → Keys](https://fal.ai/dashboard/keys)
3. Create new key and copy it
4. Free tier: ~$0.50 credits to start (~$0.01-0.03 per image)
5. **Without FAL_KEY**: Videos use gradient backgrounds instead

**At least ONE summarization API** is required (OpenRouter recommended):
- **OpenRouter** (recommended): Access to many models including free ones
- **Groq**: Fast inference with generous free tier
- **Mistral**: High-quality models with free tier
- **Gemini**: Fallback option

## System Dependencies

The video generation feature requires the following system dependencies:

### FFmpeg (Required)
Used by MoviePy for video encoding/decoding.

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt-get install ffmpeg
```

**Windows:**
Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to PATH.

### ImageMagick (Required for text overlays)
Used by MoviePy's TextClip for rendering text on videos.

**macOS:**
```bash
brew install imagemagick
```

**Ubuntu/Debian:**
```bash
sudo apt-get install imagemagick
```

**Windows:**
Download from [imagemagick.org](https://imagemagick.org/script/download.php) and add to PATH.

> **Note:** After installing ImageMagick, you may need to edit the policy.xml file to allow PDF/text operations. See [MoviePy documentation](https://zulko.github.io/moviepy/install.html) for details.

## Features

- **URL Scraping**: Paste any article URL to scrape content server-side
- **Bookmarklet**: Browser bookmarklet for one-click scraping
- **AI Summarization**: Generates TL;DR, key bullets, and video scripts using multiple AI providers (OpenRouter, Groq, Mistral, Gemini)
- **AI-Powered Video Generation**: Creates TikTok-style short-form videos with:
  - AI-generated B-roll images from FAL.ai (FLUX.1-dev model)
  - Kokoro-82M for high-quality, natural TTS voiceover
  - Fast-paced text (3-4 word chunks, TikTok style)
  - Visual changes every 2-3 seconds for engagement
  - 9:16 vertical format for TikTok/Instagram/YouTube Shorts

## Project Structure

```
scap_web/
├── app.py              # Flask backend
├── models.py           # Database models
├── summarizer.py       # Gemini AI integration
├── video_generator.py  # Video creation with MoviePy
├── bookmarklet.js      # Browser bookmarklet source
├── static/             # Frontend assets
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── instance/           # SQLite database (auto-created)
├── videos/             # Generated videos output
└── requirements.txt    # Python dependencies
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/articles` | List all articles |
| POST | `/api/scrape` | Submit scraped article data |
| POST | `/api/scrape-url` | Scrape article from URL (server-side) |
| POST | `/api/articles/:id/summarize` | Generate AI summary |
| POST | `/api/articles/:id/video` | Generate video |
| DELETE | `/api/articles/:id` | Delete article |
| GET | `/api/health` | Health check endpoint |

---

## Testing

### Run Tests

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/test_security.py -v -m security      # Security tests
pytest tests/test_api.py -v -m integration        # API integration tests
pytest tests/test_video_generator.py -v -m unit   # Unit tests
```

### Test Coverage

✅ **50 tests, 100% passing**

- **Security Tests (15):** CORS, path traversal, input validation, SQL injection
- **Integration Tests (18):** All API endpoints, error handling, workflows
- **Unit Tests (17):** Video generation, image processing, text chunking

See [TESTING_SUMMARY.md](TESTING_SUMMARY.md) for detailed test results.

---

## Production Deployment

### Docker (Recommended)

```bash
# Build and run
docker-compose up -d

# Check health
curl http://localhost:5050/api/health

# View logs
docker-compose logs -f
```

### Traditional Hosting

```bash
# Install dependencies
pip install -r requirements.txt

# Run with Gunicorn (production server)
gunicorn -c gunicorn.conf.py wsgi:app
```

### Security Checklist

Before deploying to production:

- [ ] Set `CORS_ORIGINS` to your actual domains
- [ ] Use HTTPS (never HTTP in production)
- [ ] Rotate API keys to production-specific keys
- [ ] Configure monitoring and logging
- [ ] Set up regular backups
- [ ] Enable firewall and rate limiting

See [DEPLOYMENT.md](DEPLOYMENT.md) for comprehensive deployment guide.

---

## Documentation

- [AI_VIDEO_GENERATION.md](AI_VIDEO_GENERATION.md) - Video generation details and customization
- [SECURITY_IMPROVEMENTS.md](SECURITY_IMPROVEMENTS.md) - Security hardening and fixes
- [TESTING_SUMMARY.md](TESTING_SUMMARY.md) - Test coverage and results
- [DEPLOYMENT.md](DEPLOYMENT.md) - Production deployment guide

---

## Development Workflow

This project follows the **AI-Assisted Development Workflow**:

1. ✅ **Scaffold** - Initial structure generated (~70% complete)
2. ✅ **Refine** - Real-time iteration and polish
3. ✅ **Review** - Security hardening and code quality
4. ✅ **Test & Deploy** - Comprehensive tests, deployment ready

See [.claude/skills/ai-dev-workflow.md](.claude/skills/ai-dev-workflow.md) for workflow details.

---

## License

MIT

---

## Support

For issues, questions, or feature requests, please:
1. Check existing documentation (links above)
2. Review [DEPLOYMENT.md](DEPLOYMENT.md) troubleshooting section
3. Open an issue with detailed description and logs

# Article Scraper MVP

A web application that scrapes articles, generates AI summaries using Google Gemini, and creates short-form videos with text-to-speech.

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

# AI Image Generation for Videos (Required)
HUGGINGFACE_API_KEY=your_huggingface_api_key_here  # Get from https://huggingface.co/settings/tokens
```

### Getting Your API Keys

**HuggingFace** (Required for video generation):
1. Create a free account at [HuggingFace](https://huggingface.co/join)
2. Go to [Settings → Access Tokens](https://huggingface.co/settings/tokens)
3. Create a new token (read access is sufficient)
4. Free tier includes generous limits for image generation

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
- **AI-Powered Video Generation**: Creates engaging short-form videos with:
  - AI-generated images from HuggingFace (FLUX.1-schnell model)
  - Text overlays synchronized with content
  - Professional text-to-speech using Google TTS
  - 9:16 vertical format optimized for social media

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

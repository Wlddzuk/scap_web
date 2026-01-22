# Architecture

**Analysis Date:** 2026-01-22

## Pattern Overview

**Overall:** Three-tier web application with content extraction, AI processing pipeline, and media generation

**Key Characteristics:**
- Client-server with REST API backend
- Content extraction at two layers: browser-side (bookmarklet) and server-side
- AI-powered processing pipeline (summarization → video generation)
- Stateful article tracking with SQLite database
- Media generation with external service integrations

## Layers

**Presentation Layer:**
- Purpose: User-facing dashboard and interaction interface
- Location: `static/` (HTML, CSS, JavaScript)
- Contains: Dashboard UI, form handling, article list rendering, status polling
- Depends on: REST API endpoints in Flask backend
- Used by: End users via web browser

**API Layer:**
- Purpose: RESTful interface for all operations
- Location: `app.py` (routes defined in lines 162-418)
- Contains: Route handlers, request validation, response serialization
- Depends on: Database models, business logic modules (summarizer, video_generator)
- Used by: Frontend, bookmarklet, external clients

**Business Logic Layer:**
- Purpose: Core processing and transformation
- Location: `summarizer.py`, `video_generator.py`
- Contains: AI calls (multi-provider fallback), content chunking, image generation, TTS
- Depends on: External AI/media APIs, utility functions
- Used by: API layer endpoints

**Data Layer:**
- Purpose: Persistent storage and state management
- Location: `models.py` (SQLAlchemy ORM), `instance/database.db` (SQLite)
- Contains: Article model with full metadata and processing state
- Depends on: Flask-SQLAlchemy
- Used by: All API endpoints for CRUD operations

**Content Extraction Layer:**
- Purpose: Extract article content from web pages
- Location: `app.py` (lines 30-102), `bookmarklet.js`
- Contains: HTML parsing, metadata extraction, article detection heuristics
- Depends on: BeautifulSoup (server-side), DOM API (client-side)
- Used by: `/api/scrape` and `/api/scrape-url` endpoints

## Data Flow

**Article Scraping Flow:**

1. User provides URL via bookmarklet or dashboard form
2. Content extracted (client-side via bookmarklet OR server-side via `scrape_url_content()`)
3. Article created in database with status `scraped`
4. Data returned to frontend, rendered in list

**Summarization Flow:**

1. User clicks "Summarize" button on article card
2. POST to `/api/articles/{id}/summarize`
3. Status updated to `summarizing`
4. `summarize_article()` called with multi-provider fallback:
   - Try OpenRouter (line 224)
   - Fallback to Groq (line 232)
   - Fallback to Mistral (line 240)
   - Fallback to Gemini (line 248)
5. Response parsed to extract: tldr, bullets, video_script, hashtags
6. Article fields updated, status → `summarized`
7. Frontend refetched, card expanded to show summary

**Video Generation Flow:**

1. User clicks "Generate Video" on summarized article
2. POST to `/api/articles/{id}/video`
3. Status updated to `generating_video`
4. `generate_video()` called (line 576 in video_generator.py):
   - Clean script for TTS (remove bracket tags)
   - Generate voiceover using Kokoro TTS (fallback to gTTS)
   - Extract subjects and visual style from title/script using Groq
   - Generate 10 image prompts using Groq (story-aware)
   - Generate images using FAL.ai FLUX (fallback to gradient backgrounds)
   - Chunk script for pacing (visual timing, not captions)
   - Create hook clip (dramatic opening) + main clips
   - Concatenate clips with voiceover audio
   - Render final MP4 to `static/videos/`
5. Video path stored in database, status → `video_done`
6. Video URL returned to frontend, player embedded

**State Management:**

- Articles tracked through explicit status field: `scraped → summarizing → summarized → generating_video → video_done` (or `failed` at any step)
- All timestamps recorded: `scraped_at`, `summarized_at`, `video_generated_at`
- All metadata persisted: url, title, content, hero_image, tldr, bullets, video_script, hashtags, video_path

## Key Abstractions

**Article Entity:**
- Purpose: Central domain object representing a web article through its processing lifecycle
- Examples: `models.py` (lines 9-58)
- Pattern: SQLAlchemy model with JSON serialization method `to_dict()`, rich status tracking

**Summarizer Module:**
- Purpose: Unified interface to multiple LLM providers with graceful fallback
- Examples: `summarizer.py` (lines 211-252)
- Pattern: Provider-specific functions (openrouter, groq, mistral, gemini) tried in order, shared prompt template, JSON response parsing

**Video Generator Module:**
- Purpose: End-to-end video creation from script to playable MP4
- Examples: `video_generator.py` (lines 576-697)
- Pattern: Decomposed into image generation, TTS, chunking, clip creation, assembly phases with error handling and resource cleanup

**Content Extractor:**
- Purpose: Heuristic-based article content discovery from arbitrary HTML
- Examples: `app.py` (lines 30-102), `bookmarklet.js` (lines 20-100)
- Pattern: Progressive fallback strategy (article tag → semantic tags → generic containers → full body) for both server and client implementations

## Entry Points

**Web Dashboard:**
- Location: `static/index.html` served from `app.route('/', line 166)`
- Triggers: Browser visit to `http://localhost:5050`
- Responsibilities: Render article list, URL input form, article cards with action buttons

**REST API:**
- Location: Routes defined in `app.py` (lines 162-418)
- Triggers: HTTP POST/GET/DELETE to `/api/*`
- Responsibilities: Request routing, validation, database mutations, response serialization

**Bookmarklet:**
- Location: `bookmarklet.js`
- Triggers: User clicks bookmarklet on article page
- Responsibilities: Extract content from current page, POST to backend, open dashboard

**Server Entry:**
- Location: `app.py` (lines 424-432)
- Triggers: `python app.py` or gunicorn
- Responsibilities: Flask initialization, CORS setup, DB initialization, API key validation

## Error Handling

**Strategy:** Layered fallback with graceful degradation

**Patterns:**

- **Summarization Errors** (lines 211-252 in summarizer.py): Try each AI provider in sequence, log each failure, raise exception only if all fail. Client sees error toast.

- **Image Generation Errors** (lines 56-103 in video_generator.py): Generate images with 2 retry attempts, fall back to gradient background if FAL.ai fails.

- **TTS Errors** (lines 148-179 in video_generator.py): Try Kokoro first, fall back to gTTS if unavailable.

- **HTTP Request Errors** (app.py lines 299-304): Catch requests.RequestException specifically, log with context, return user-friendly error message.

- **Video Generation Errors** (lines 666-697 in video_generator.py): Comprehensive try-except with resource cleanup in finally block (close audio, video, clips).

- **Database Errors**: 404 responses for missing articles (`Article.query.get_or_404()`), transaction rollback if commit fails.

## Cross-Cutting Concerns

**Logging:**
- Approach: Python logging with configured formatters (app.py lines 22-27)
- Channels: Console output with print() for progress, structured logging for errors
- Strategy: Verbose progress in video_generator.py (video generation steps), error logging in summarizer.py (provider attempts)

**Validation:**

- **URL Validation:** Content length checks (>100 chars), URL format validation via requests library
- **API Input:** JSON schema implicit in endpoint documentation (required fields checked explicitly)
- **File Operations:** Path sanitization with `secure_filename()` for video serving (line 182)

**Authentication:**

- Approach: None enforced (development/internal use)
- API Key Management: Environment variables loaded via dotenv, validated at startup (lines 133-159)
- CORS Setup: Whitelist of origins from env var, configurable per deployment (lines 110-118)

**Resource Management:**

- **Memory:** Video clips and audio closed in finally block (lines 673-690)
- **Disk:** Temp audio files deleted after use (lines 693-697)
- **Concurrency:** Single-threaded Flask development mode; gunicorn handles production load

---

*Architecture analysis: 2026-01-22*

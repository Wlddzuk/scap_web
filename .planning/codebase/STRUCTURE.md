# Codebase Structure

**Analysis Date:** 2026-01-22

## Directory Layout

```
scap_web/
├── app.py                       # Flask application, API endpoints, content extraction
├── models.py                    # SQLAlchemy ORM - Article model
├── summarizer.py                # AI summarization with multi-provider fallback
├── video_generator.py           # TikTok video generation pipeline
├── bookmarklet.js               # Browser extension for article extraction
├── gunicorn.conf.py             # Production WSGI server config
├── docker-compose.yml           # Docker orchestration
├── Dockerfile                   # Container build instructions
├── .env                         # Environment variables (git-ignored)
├── .env.example                 # Template for environment setup
├── .dockerignore                # Docker build exclusions
│
├── static/                      # Frontend assets and videos
│   ├── index.html              # Main dashboard page
│   ├── app.js                  # Frontend JavaScript (state, API calls, rendering)
│   ├── styles.css              # Dashboard styling
│   ├── anime.min.js            # Animation library
│   ├── bookmarklet_install.html # Bookmarklet installer page
│   └── videos/                 # Generated MP4 videos (output directory)
│
├── instance/                    # SQLite database
│   └── database.db             # Persistent article storage
│
├── tests/                       # Test suite
│   ├── conftest.py             # Pytest fixtures and configuration
│   ├── test_api.py             # API endpoint integration tests
│   ├── test_security.py        # Security validation tests
│   ├── test_video_generator.py # Video generation unit tests
│   └── __init__.py             # Package marker
│
├── .planning/
│   └── codebase/               # GSD analysis documents (this directory)
│
└── [Documentation]
    ├── README.md               # Project overview and quick start
    ├── DEPLOYMENT.md           # Deployment instructions
    ├── SECURITY_IMPROVEMENTS.md # Security audit findings
    ├── AI_VIDEO_GENERATION.md  # Video generation technical details
    ├── TESTING_SUMMARY.md      # Test coverage report
    └── WAN_I2V_IMPLEMENTATION.md # Image-to-video research
```

## Directory Purposes

**Root:**
- Purpose: Application entry point and configuration
- Contains: Flask app initialization, environment config, Docker setup
- Key files: `app.py` (main server), `.env` (secrets)

**static/:**
- Purpose: Frontend UI and static assets
- Contains: HTML dashboard, JavaScript interactivity, CSS styling, animation libraries
- Key files: `index.html` (entry point), `app.js` (API integration), `styles.css` (layout/design)
- Generated: `videos/` subdirectory populated at runtime

**instance/:**
- Purpose: Local SQLite database persistence
- Contains: SQLite database file (auto-created on first run)
- Key files: `database.db` (read/write by Flask)
- Generated: Yes (created by SQLAlchemy on startup)
- Committed: No (in .gitignore)

**tests/:**
- Purpose: Automated test coverage
- Contains: Integration tests, security tests, unit tests
- Key files: `conftest.py` (fixtures), `test_api.py` (main test suite)
- Run with: `pytest` command

## Key File Locations

**Entry Points:**
- `app.py`: Flask app initialization and all API routes (line 105+)
- `static/index.html`: Dashboard HTML loaded on GET /
- `bookmarklet.js`: Content extraction script for browsers

**Configuration:**
- `.env`: Runtime secrets (API keys, database URI, CORS origins)
- `.env.example`: Template showing required environment variables
- `gunicorn.conf.py`: Production WSGI server settings
- `docker-compose.yml`: Multi-container orchestration

**Core Logic:**
- `models.py`: Article ORM model (lines 9-58)
- `summarizer.py`: AI summarization logic (lines 211-252 main entry)
- `video_generator.py`: Video generation pipeline (lines 576-697 main entry)

**Frontend:**
- `static/app.js`: All frontend API calls and state management
- `static/styles.css`: Complete UI styling
- `static/index.html`: HTML structure

**Testing:**
- `tests/conftest.py`: Pytest fixtures (Flask client, test DB, sample data)
- `tests/test_api.py`: Endpoint integration tests (CRUD, scraping, video)
- `tests/test_security.py`: Security validation (CSP, CORS, injection)

## Naming Conventions

**Files:**
- Python modules: `lowercase_with_underscores.py` (e.g., `video_generator.py`, `summarizer.py`)
- Frontend files: `lowercase.js`, `lowercase.css` (e.g., `app.js`, `styles.css`)
- Test files: `test_<module>.py` (e.g., `test_api.py`, `test_security.py`)
- Database: `database.db` in `instance/` directory

**Directories:**
- Feature modules: Root level (e.g., `video_generator.py` not `video/generator.py`)
- Static assets: `static/` subdirectory
- Tests: `tests/` with flat structure (one file per major feature)
- Generated output: `static/videos/` for MP4 files

**Functions:**
- Python: `snake_case` (e.g., `summarize_article()`, `generate_video()`, `scrape_url_content()`)
- JavaScript: `camelCase` (e.g., `fetchArticles()`, `scrapeUrl()`, `expandedArticles`)

**Variables:**
- Python: `snake_case` (e.g., `article_id`, `api_key`, `video_path`)
- JavaScript: `camelCase` (e.g., `urlInput`, `articleId`, `expandedArticles`)
- Constants: `UPPERCASE_WITH_UNDERSCORES` (e.g., `VIDEO_WIDTH`, `DEFAULT_CHUNK_DURATION`)

**API Routes:**
- Pattern: `/api/<resource>/<id>/<action>`
- Examples: `/api/articles`, `/api/articles/1`, `/api/articles/1/summarize`, `/api/articles/1/video`
- HTTP Methods: GET (retrieve), POST (create/action), DELETE (remove)

**Database:**
- Table: `articles` (singular, lowercase)
- Columns: `snake_case` (e.g., `article_id`, `hero_image`, `video_script`)
- Foreign keys: `<resource>_id` convention

## Where to Add New Code

**New Feature (new endpoint + logic):**
- Backend logic: `app.py` (add new route + handler)
- Supporting logic: Extract to new module at root if >100 lines (e.g., `processor.py`)
- Tests: Add test class to `tests/test_api.py` or create `tests/test_<feature>.py`
- Frontend: Add methods to `static/app.js` for API calls, update `static/index.html` for UI

**New Component/UI Element:**
- Implementation: `static/index.html` (add HTML markup)
- Styling: `static/styles.css` (add CSS classes)
- Interaction: `static/app.js` (add event handlers and state management)
- Server support: If needs new data, add to `models.py` and API response

**Utilities/Helpers:**
- Shared Python helpers: Add function to nearest existing module or create `utils.py`
- Shared JavaScript helpers: Add to `static/app.js` at top level (not wrapped in class)
- Content extraction: Expand heuristics in `app.py` lines 30-102 or `bookmarklet.js`

## Special Directories

**static/videos/:**
- Purpose: Stores generated MP4 output files
- Generated: Yes (created at runtime by video_generator.py)
- Committed: No (in .gitignore)
- Lifetime: Persists until manual cleanup
- Served: Via Flask route `/videos/<filename>` (line 178-185 in app.py)

**instance/:**
- Purpose: Instance-specific data (database, local config)
- Generated: Yes (database.db auto-created on first run)
- Committed: No (Flask convention, in .gitignore)
- Lifetime: Persists until manual deletion
- Usage: Contains full article records and processing state

**.planning/codebase/:**
- Purpose: GSD (Getting Started Device) analysis documents
- Generated: Yes (created by GSD commands)
- Committed: Yes (tracked in git)
- Contents: ARCHITECTURE.md, STRUCTURE.md, CONVENTIONS.md, TESTING.md, STACK.md, INTEGRATIONS.md, CONCERNS.md

**tests/:**
- Purpose: All automated tests
- Generated: No (source code)
- Committed: Yes
- Run: `pytest` (discovers all test_*.py files)
- Output: .pytest_cache/ (generated, git-ignored)

## Project-Wide Patterns

**API Response Format:**
All endpoints return JSON with consistent structure:
```python
# Success
{
    "message": "Operation description",
    "article": { ... },  # or "articles": [ ... ]
    "count": N
}

# Error
{
    "error": "Human-readable error message"
}
```

**Status Transitions:**
Article statuses follow strict state machine (models.py line 22):
```
scraped → summarizing → summarized → generating_video → video_done
                ↓                           ↓
              failed                     failed
```

**Environment-Based Configuration:**
- Development: Uses .env with localhost URLs and generous defaults
- Production: Expects environment variables (CORS_ORIGINS, DATABASE_URI, API keys)
- Testing: Uses pytest fixtures that mock external services

---

*Structure analysis: 2026-01-22*

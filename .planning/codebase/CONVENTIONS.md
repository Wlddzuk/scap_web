# Coding Conventions

**Analysis Date:** 2026-01-22

## Naming Patterns

**Files:**
- Snake case for Python files: `app.py`, `models.py`, `summarizer.py`, `video_generator.py`
- Test files follow pattern: `test_*.py` (e.g., `test_api.py`, `test_security.py`)
- Single word or underscore-separated descriptive names

**Functions:**
- Snake case throughout: `scrape_url_content()`, `validate_api_keys()`, `list_articles()`, `serve_dashboard()`
- Descriptive verbs as prefix: `generate_`, `summarize_`, `parse_`, `create_`, `serve_`
- Underscores for readability: `summarize_article_endpoint()`, `generate_video_endpoint()`, `resize_and_crop_image()`

**Variables:**
- Snake case: `api_keys`, `db_path`, `video_path`, `allowed_origins`
- Boolean prefixes with `is_`, `has_`, or full descriptive names
- Loop variables are descriptive: `article`, `chunk`, `attempt` not `i`, `j`, `x`

**Types:**
- Class names in PascalCase: `Article`, `Test*`, `Groq`, `KPipeline`
- Constants in UPPER_SNAKE_CASE: `VIDEO_WIDTH`, `VIDEO_HEIGHT`, `FPS`, `HOOK_DURATION`, `MIN_CHUNK_DURATION`, `DEFAULT_WORDS_PER_CHUNK`, `RETRY_ATTEMPTS`

## Code Style

**Formatting:**
- 4-space indentation throughout
- Max line length appears practical (not enforced but observed)
- Blank lines between function definitions and logical sections
- Comments before section headers: `# ============================================================`

**Linting:**
- Framework in place via requirements-dev.txt: `black>=23.7.0`, `flake8>=6.1.0`, `pylint>=3.0.0`
- Security linting with `bandit>=1.7.5`
- Type checking with `mypy>=1.5.0`
- Not actively enforced in pre-commit (no `.git/hooks` evidence), but available

**Import Style:**
- Standard imports first: `os`, `json`, `re`, `logging`, `datetime`
- Third-party imports second: `flask`, `requests`, `beautifulsoup4`
- Local imports last: `from models import db, Article`
- No wildcard imports observed

## Docstrings

**Module Level:**
- Triple-quoted docstrings at file start: `"""Flask API for Article Scraper MVP."""`
- Describes purpose and key characteristics

**Function Level:**
- Triple-quoted docstrings immediately after function definition
- Single-line summary for simple functions
- Multi-line format for complex functions with Expected JSON/Returns sections
- Example from `scrape_url_content()`:
  ```python
  """Fetch and parse article content from a URL server-side."""
  ```
- Example from `scrape_article()`:
  ```python
  """
  Receive scraped article content from the bookmarklet.

  Expected JSON:
  {
      "url": "https://example.com/article",
      "title": "Article Title",
      ...
  }
  """
  ```

**Type Hints:**
- Used in function signatures: `def get_prompt(title: str, content: str) -> str:`
- Used consistently in summarizer.py and video_generator.py
- Return types specified: `-> dict`, `-> Image.Image`, `-> str`
- Optional typing not extensively used, but exceptions exist

## Comment Strategy

**When to Comment:**
- Before significant algorithm sections: `# ============================================`
- Complex conditional logic: `# Sanitize filename to prevent directory traversal attacks`
- Fallback behavior: `# Fallback to body text`
- Important config values with reasoning

**Inline Comments:**
- Used sparingly, primarily for non-obvious logic
- Placed after code on same line or on preceding line for blocks
- Example: `# JSON array stored as text` on model field definitions

**Print Statements for Logging:**
- Heavy use of structured logging in app.py: `logger.info()`, `logger.error()`
- Also uses print() with prefix tags in video_generator.py: `print("[Image] Generating...")`, `print("[TTS] ✅ Audio saved")`
- Emoji prefixes in fallback print statements for visibility

## Error Handling

**Pattern - Try/Except with Logging:**
```python
try:
    # Operation
    result = summarize_article(article.title, article.content)
    article.tldr = result['tldr']
except Exception as e:
    logger.error(f"Failed to summarize article {article_id}: {str(e)}", exc_info=True)
    article.status = 'failed'
    db.session.commit()
    return jsonify({'error': 'Failed to generate summary. Please try again later.'}), 500
```

**Generic Error Messages:**
- User-facing errors are generic and don't expose internals
- Example: `'Failed to generate summary. Please try again later.'` instead of actual exception
- Logging captures full details with `exc_info=True`

**Request Exception Handling:**
```python
except requests.exceptions.RequestException as e:
    logger.error(f"Failed to fetch URL {url}: {str(e)}")
    return jsonify({'error': 'Failed to fetch the URL. Please check the URL and try again.'}), 400
```

**Fallback Chains:**
- Multiple API providers tried in sequence: OpenRouter → Groq → Mistral → Gemini
- Each wrapped in try/except with error tracking
- Example from `summarize_article()` in summarizer.py

## JSON Handling

**Pattern:**
```python
import json

# Storing JSON as text in database
article.bullets = json.dumps(result['bullets'])
article.hashtags = json.dumps(result.get('hashtags', []))

# Retrieving JSON from database
'bullets': json.loads(self.bullets) if self.bullets else None,
```

**Response Format:**
- All API responses use `jsonify()` with consistent structure
- Error responses: `{'error': 'message'}`
- Success responses include message and data: `{'message': '...', 'article': {...}}`

## Database Interactions

**Pattern - SQLAlchemy with Flask:**
```python
article = Article(
    url=url,
    title=title,
    content=content,
    status='scraped'
)
db.session.add(article)
db.session.commit()
```

**Query Pattern:**
```python
existing = Article.query.filter_by(url=url).first()
articles = Article.query.order_by(Article.scraped_at.desc()).all()
article = Article.query.get_or_404(article_id)
```

**Model Methods:**
- `to_dict()` method on models for serialization to JSON
- Handles JSON parsing/stringifying on access

## Configuration Management

**Environment Variables:**
- Loaded with `load_dotenv()` at module start
- Accessed via `os.getenv()` with defaults: `os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5050')`
- Critical vars checked on app init: `validate_api_keys()`

**Flask App Config:**
```python
app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
```

## Logging

**Framework:** Python's built-in logging module

**Setup:**
```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
```

**Usage:**
- Info level for normal operations: `logger.info("✓ Configured API keys: ...")`
- Warning level for configuration issues: `logger.warning("⚠️  No summarization API keys found!")`
- Error level for exceptions: `logger.error(f"Failed to...: {str(e)}")`
- Include `exc_info=True` for full stack traces: `logger.error(..., exc_info=True)`

## Input Validation

**Pattern:**
```python
data = request.get_json()

if not data:
    return jsonify({'error': 'No data provided'}), 400

url = data.get('url')
if not url:
    return jsonify({'error': 'URL is required'}), 400

if not content:
    return jsonify({'error': 'Content is required'}), 400
```

**Checks:**
- Presence validation (required fields)
- Content length validation: `if not scraped['content'] or len(scraped['content']) < 100:`
- Path sanitization: `secure_filename()` from werkzeug
- URL validation through HTTP GET attempt

## Status Values

**Standardized Article Status Values:**
- `'scraped'` - Initial state after scraping
- `'summarizing'` - Processing summary
- `'summarized'` - Summary complete
- `'generating_video'` - Video rendering
- `'video_done'` - Video generated successfully
- `'failed'` - Error during processing

These are set in `models.py` comment and enforced throughout app.py endpoints.

---

*Convention analysis: 2026-01-22*

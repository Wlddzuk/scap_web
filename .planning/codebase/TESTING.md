# Testing Patterns

**Analysis Date:** 2026-01-22

## Test Framework

**Runner:**
- pytest 7.4.0+
- Config: `pytest.ini` at project root

**Assertion Library:**
- Built-in pytest assertions

**Run Commands:**
```bash
pytest                    # Run all tests
pytest -v                 # Verbose output
pytest -k pattern         # Run tests matching pattern
pytest --tb=short         # Short traceback format
pytest -m integration     # Run only integration tests
pytest -m unit            # Run only unit tests
pytest -m security        # Run only security tests
```

**Configuration from pytest.ini:**
```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    -v
    --tb=short
    --strict-markers
    --disable-warnings
markers =
    security: Security-related tests
    integration: Integration tests
    unit: Unit tests
    slow: Slow running tests
```

## Test File Organization

**Location:**
- Co-located in `tests/` directory at project root
- Structure: `tests/conftest.py`, `tests/test_api.py`, `tests/test_security.py`, `tests/test_video_generator.py`

**Naming:**
- Files: `test_*.py`
- Classes: `Test*` (e.g., `TestArticleEndpoints`, `TestScrapeURLEndpoint`, `TestImageGeneration`)
- Functions: `test_*` (e.g., `test_create_article_via_scrape`, `test_cors_allows_whitelisted_origins`)

**Structure:**
```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── __init__.py
├── test_api.py                    # API endpoint tests
├── test_security.py               # Security and validation tests
└── test_video_generator.py        # Video generation unit tests
```

## Test Structure

**Suite Organization - from test_api.py:**
```python
@pytest.mark.integration
class TestArticleEndpoints:
    """Test article CRUD endpoints."""

    def test_create_article_via_scrape(self, client):
        """Test creating an article via the scrape endpoint."""
        response = client.post('/api/scrape', json={
            'url': 'https://example.com/article1',
            'title': 'Test Article 1',
            'content': 'This is the content of test article 1.',
            'hero_image': 'https://example.com/image.jpg',
            'site_name': 'Example'
        })

        assert response.status_code == 201
        data = response.get_json()
        assert data['message'] == 'Article scraped successfully'
        assert data['article']['title'] == 'Test Article 1'
        assert data['article']['status'] == 'scraped'
```

**Patterns:**

1. **Class-based Organization:**
   - Groups related tests into classes
   - One `@pytest.mark` per class for categorization
   - Descriptive class names with docstrings

2. **Setup/Teardown:**
   - Fixtures handle setup in `conftest.py`
   - Database fixtures create temp DB and clean up
   - No explicit teardown; pytest fixtures manage lifecycle
   - Example from conftest.py:
     ```python
     @pytest.fixture
     def app():
         """Create and configure a test Flask application."""
         db_fd, db_path = tempfile.mkstemp()

         flask_app.config.update({
             'TESTING': True,
             'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_path}',
             'SQLALCHEMY_TRACK_MODIFICATIONS': False,
         })

         with flask_app.app_context():
             db.create_all()
             yield flask_app
             db.drop_all()

         os.close(db_fd)
         os.unlink(db_path)
     ```

3. **Assertion Pattern:**
   - Direct pytest assertions: `assert response.status_code == 201`
   - Response parsing: `data = response.get_json()`
   - Chain assertions on related conditions
   - Check both response code and content

## Fixtures

**Core Fixtures - from tests/conftest.py:**

```python
@pytest.fixture
def app():
    """Create and configure a test Flask application."""
    # Creates temp database, configures Flask for testing
    # Yields app context, cleans up after test
```

```python
@pytest.fixture
def client(app):
    """Create a test client for the app."""
    return app.test_client()
    # Used for making HTTP requests to endpoints
```

```python
@pytest.fixture
def runner(app):
    """Create a test CLI runner."""
    return app.test_cli_runner()
    # Used for testing CLI commands (if any)
```

```python
@pytest.fixture
def sample_article(app):
    """Create a sample article in the database."""
    # Creates Article with status='scraped'
    # Returns article_id
    # Used in endpoint tests that expect pre-existing data
```

```python
@pytest.fixture
def summarized_article(app):
    """Create a summarized article with video script."""
    # Creates Article with status='summarized' and populated fields
    # Returns article_id
    # Used in video generation and downstream tests
```

```python
@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    monkeypatch.setenv('GEMINI_API_KEY', 'test_gemini_key')
    monkeypatch.setenv('GROQ_API_KEY', 'test_groq_key')
    monkeypatch.setenv('FAL_KEY', 'test_fal_key')
    monkeypatch.setenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5050')
```

## Mocking

**Framework:** `unittest.mock` (built-in Python)

**Mocking Pattern - from test_api.py:**
```python
@patch('app.requests.get')
def test_scrape_url_success(self, mock_get, client):
    """Test successful URL scraping."""
    # Mock the HTTP response
    mock_response = MagicMock()
    mock_response.text = '''
        <html>
            <head><title>Test Page</title></head>
            <body>
                <article>
                    <h1>Test Article Title</h1>
                    <p>This is a test paragraph with enough content.</p>
                </article>
            </body>
        </html>
    '''
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    response = client.post('/api/scrape-url', json={
        'url': 'https://example.com/article'
    })

    assert response.status_code == 201
```

**Mocking Pattern - Exceptions:**
```python
@patch('app.requests.get')
def test_scrape_url_timeout(self, mock_get, client):
    """Test URL scraping handles timeouts."""
    import requests
    mock_get.side_effect = requests.exceptions.Timeout('Timeout')

    response = client.post('/api/scrape-url', json={
        'url': 'https://example.com/slow'
    })

    assert response.status_code == 400
    data = response.get_json()
    assert 'error' in data
```

**What to Mock:**
- External API calls: `requests.get()`, `requests.post()`, Groq/Gemini clients
- Time-dependent operations
- Files and I/O operations
- Environment variable lookups
- Paths and network operations

**What NOT to Mock:**
- Flask test client (use the real `client` fixture)
- SQLAlchemy database operations (use temp SQLite DB)
- Built-in Python functions (str, json, etc.)
- Internal application logic (test the real code)

## Test Categories

**Unit Tests (@pytest.mark.unit):**
- Test individual functions in isolation
- Location: Primarily in `test_video_generator.py`
- Example: `test_create_gradient_background()`, `test_clean_script_for_tts()`
- Scope: Single function behavior, edge cases
- Mocking: Minimal; focus on pure function logic

**Integration Tests (@pytest.mark.integration):**
- Test API endpoints end-to-end
- Location: `test_api.py`
- Example: `test_create_article_via_scrape()`, `test_list_articles()`
- Scope: HTTP request/response, database interaction, request validation
- Mocking: External services (requests), AI APIs

**Security Tests (@pytest.mark.security):**
- Test protection against attacks and malicious input
- Location: `test_security.py`
- Categories:
  - CORS restrictions
  - Path traversal prevention
  - SQL injection prevention
  - Input validation
  - Error message sanitization
  - Resource limits

**Example from test_security.py:**
```python
@pytest.mark.security
class TestPathTraversalProtection:
    """Test protection against path traversal attacks."""

    def test_path_traversal_blocked_double_dots(self, client):
        """Test that ../ path traversal is blocked."""
        response = client.get('/videos/../../../etc/passwd')
        assert response.status_code == 400
        assert b'Invalid filename' in response.data
```

## Test Patterns

**API Endpoint Testing Pattern:**
```python
def test_create_article_via_scrape(self, client):
    """Test creating an article via the scrape endpoint."""
    # 1. Prepare request data
    response = client.post('/api/scrape', json={
        'url': 'https://example.com/article1',
        'title': 'Test Article 1',
        'content': 'This is the content of test article 1.',
    })

    # 2. Assert response code
    assert response.status_code == 201

    # 3. Parse and verify response content
    data = response.get_json()
    assert data['message'] == 'Article scraped successfully'
    assert data['article']['title'] == 'Test Article 1'
    assert data['article']['status'] == 'scraped'
```

**State Verification Pattern:**
```python
def test_delete_article(self, client, sample_article):
    """Test deleting an article."""
    response = client.delete(f'/api/articles/{sample_article}')

    assert response.status_code == 200
    data = response.get_json()
    assert 'deleted' in data['message'].lower()

    # Verify it's actually gone
    get_response = client.get(f'/api/articles/{sample_article}')
    assert get_response.status_code == 404
```

**Error Handling Pattern:**
```python
def test_summarize_article_failure(self, mock_summarize, client, sample_article):
    """Test summarization failure handling."""
    mock_summarize.side_effect = Exception('API Error')

    response = client.post(f'/api/articles/{sample_article}/summarize')

    assert response.status_code == 500
    data = response.get_json()
    assert 'error' in data
    # Verify internal error is not exposed
    assert 'API Error' not in data['error']
```

## Coverage

**Framework:** pytest-cov (installed in requirements-dev.txt)

**Run Coverage:**
```bash
pytest --cov=.              # Coverage for all modules
pytest --cov=. --cov-report=html  # Generate HTML report
```

**Current Coverage:**
- Not enforced with thresholds in pytest.ini
- Focus areas tested:
  - API endpoints: All CRUD operations covered
  - Security: Input validation, path traversal, CORS, error handling
  - Video generation: Image processing, script cleaning, chunking
  - Error scenarios: Timeouts, invalid input, missing summarization

**Gaps Observed:**
- `summarizer.py` fallback chain (OpenRouter → Groq → Mistral → Gemini) not tested (uses real API calls)
- `video_generator.py` full video generation not tested (resource-intensive)
- Database transaction edge cases not explicitly tested

## Testing Dependencies

**From requirements-dev.txt:**
```
pytest>=7.4.0
pytest-cov>=4.1.0
pytest-flask>=1.2.0
pytest-mock>=3.11.1
```

**Additional tooling in dev:**
- `black` for code formatting
- `flake8` for linting
- `pylint` for code quality
- `bandit` for security analysis
- `mypy` for type checking

## Running Tests

**All tests:**
```bash
pytest
```

**Specific test class:**
```bash
pytest tests/test_api.py::TestArticleEndpoints
```

**Specific test function:**
```bash
pytest tests/test_api.py::TestArticleEndpoints::test_create_article_via_scrape
```

**Only integration tests:**
```bash
pytest -m integration
```

**Only security tests:**
```bash
pytest -m security
```

**Verbose with output:**
```bash
pytest -v -s
```

---

*Testing analysis: 2026-01-22*

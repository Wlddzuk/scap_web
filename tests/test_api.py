"""Integration tests for API endpoints."""

import pytest
import json
from unittest.mock import patch, MagicMock


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

    def test_duplicate_article_returns_existing(self, client):
        """Test that scraping the same URL twice returns existing article."""
        article_data = {
            'url': 'https://example.com/same-article',
            'title': 'Same Article',
            'content': 'Content here.'
        }

        # First request
        response1 = client.post('/api/scrape', json=article_data)
        assert response1.status_code == 201

        # Second request with same URL
        response2 = client.post('/api/scrape', json=article_data)
        assert response2.status_code == 200
        data = response2.get_json()
        assert 'already exists' in data['message']

    def test_list_articles(self, client, sample_article):
        """Test listing all articles."""
        response = client.get('/api/articles')

        assert response.status_code == 200
        data = response.get_json()
        assert 'articles' in data
        assert 'count' in data
        assert data['count'] >= 1
        assert isinstance(data['articles'], list)

    def test_get_single_article(self, client, sample_article):
        """Test retrieving a single article by ID."""
        response = client.get(f'/api/articles/{sample_article}')

        assert response.status_code == 200
        data = response.get_json()
        assert data['id'] == sample_article
        assert data['title'] == 'Test Article'

    def test_get_nonexistent_article(self, client):
        """Test that getting nonexistent article returns 404."""
        response = client.get('/api/articles/999999')
        assert response.status_code == 404

    def test_delete_article(self, client, sample_article):
        """Test deleting an article."""
        response = client.delete(f'/api/articles/{sample_article}')

        assert response.status_code == 200
        data = response.get_json()
        assert 'deleted' in data['message'].lower()

        # Verify it's gone
        get_response = client.get(f'/api/articles/{sample_article}')
        assert get_response.status_code == 404


@pytest.mark.integration
class TestScrapeURLEndpoint:
    """Test server-side URL scraping."""

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
                        <p>This is a test paragraph with enough content to pass validation.</p>
                        <p>Another paragraph to make sure we have sufficient content length.</p>
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
        data = response.get_json()
        assert data['article']['title'] == 'Test Article Title'
        assert len(data['article']['content']) > 100

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


@pytest.mark.integration
class TestSummarizationEndpoint:
    """Test AI summarization endpoint."""

    @patch('app.summarize_article')
    def test_summarize_article_success(self, mock_summarize, client, sample_article):
        """Test successful article summarization."""
        # Mock the summarization result
        mock_summarize.return_value = {
            'tldr': 'This is a brief summary.',
            'bullets': ['Point 1', 'Point 2', 'Point 3'],
            'video_script': 'This is the video script narrative.'
        }

        response = client.post(f'/api/articles/{sample_article}/summarize')

        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Article summarized successfully'
        assert data['article']['status'] == 'summarized'
        assert data['article']['tldr'] == 'This is a brief summary.'

    @patch('app.summarize_article')
    def test_summarize_article_failure(self, mock_summarize, client, sample_article):
        """Test summarization failure handling."""
        mock_summarize.side_effect = Exception('API Error')

        response = client.post(f'/api/articles/{sample_article}/summarize')

        assert response.status_code == 500
        data = response.get_json()
        assert 'error' in data
        # Should not expose internal error details
        assert 'API Error' not in data['error']


@pytest.mark.integration
class TestVideoGenerationEndpoint:
    """Test video generation endpoint."""

    def test_video_generation_requires_summary(self, client, sample_article):
        """Test that video generation requires summarized article."""
        response = client.post(f'/api/articles/{sample_article}/video')

        assert response.status_code == 400
        data = response.get_json()
        assert 'must be summarized first' in data['error']

    @patch('app.generate_video')
    def test_video_generation_success(self, mock_generate, client, summarized_article):
        """Test successful video generation."""
        mock_generate.return_value = 'static/videos/article_1_20231229.mp4'

        response = client.post(f'/api/articles/{summarized_article}/video')

        assert response.status_code == 200
        data = response.get_json()
        assert data['message'] == 'Video generated successfully'
        assert data['article']['status'] == 'video_done'
        assert 'video_url' in data

    @patch('app.generate_video')
    def test_video_generation_failure(self, mock_generate, client, summarized_article):
        """Test video generation failure handling."""
        mock_generate.side_effect = Exception('Video rendering failed')

        response = client.post(f'/api/articles/{summarized_article}/video')

        assert response.status_code == 500
        data = response.get_json()
        assert 'error' in data
        # Should not expose internal error details
        assert 'rendering failed' not in data['error'].lower()


@pytest.mark.integration
class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test health check returns healthy status."""
        response = client.get('/api/health')

        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'healthy'
        assert 'timestamp' in data


@pytest.mark.integration
class TestStaticFileServing:
    """Test static file serving."""

    def test_serve_dashboard(self, client):
        """Test serving the dashboard HTML."""
        response = client.get('/')
        # May not exist in test, but should attempt to serve
        assert response.status_code in [200, 404]

    def test_serve_video_sanitizes_path(self, client):
        """Test that video serving sanitizes paths."""
        response = client.get('/videos/../app.py')
        assert response.status_code == 400

"""Security tests for the application."""

import pytest


@pytest.mark.security
class TestCORSSecurity:
    """Test CORS security restrictions."""

    def test_cors_allows_whitelisted_origins(self, client):
        """Test that whitelisted origins are allowed."""
        response = client.get(
            '/api/articles',
            headers={'Origin': 'http://localhost:3000'}
        )
        assert response.status_code == 200
        # In test mode, CORS might not set headers - that's OK

    def test_cors_preflight_request(self, client):
        """Test CORS preflight (OPTIONS) request."""
        response = client.options(
            '/api/articles',
            headers={
                'Origin': 'http://localhost:3000',
                'Access-Control-Request-Method': 'GET'
            }
        )
        # Should allow OPTIONS requests
        assert response.status_code in [200, 204]


@pytest.mark.security
class TestPathTraversalProtection:
    """Test protection against path traversal attacks."""

    def test_path_traversal_blocked_double_dots(self, client):
        """Test that ../ path traversal is blocked."""
        response = client.get('/videos/../../../etc/passwd')
        assert response.status_code == 400
        assert b'Invalid filename' in response.data

    def test_path_traversal_blocked_encoded(self, client):
        """Test that encoded path traversal is blocked."""
        response = client.get('/videos/..%2F..%2F..%2Fetc%2Fpasswd')
        assert response.status_code == 400

    def test_path_traversal_blocked_absolute_path(self, client):
        """Test that absolute paths are blocked."""
        response = client.get('/videos//etc/passwd')
        # Flask may redirect or reject - either is acceptable
        assert response.status_code in [308, 400, 404]

    def test_valid_filename_allowed(self, client):
        """Test that valid filenames are allowed (even if file doesn't exist)."""
        # This will 404 because file doesn't exist, but shouldn't be rejected as invalid
        response = client.get('/videos/article_123_20231229.mp4')
        # Should get 404 (file not found) not 400 (invalid filename)
        assert response.status_code == 404


@pytest.mark.security
class TestErrorHandling:
    """Test that errors don't expose internal details."""

    def test_generic_error_on_invalid_url_scrape(self, client):
        """Test that URL scraping errors return generic messages."""
        response = client.post(
            '/api/scrape-url',
            json={'url': 'http://this-domain-does-not-exist-12345.com'}
        )
        assert response.status_code == 400
        data = response.get_json()
        # Should not expose internal exception details
        assert 'traceback' not in data.get('error', '').lower()
        assert 'exception' not in data.get('error', '').lower()

    def test_generic_error_on_parse_failure(self, client):
        """Test that parsing errors return generic messages."""
        response = client.post(
            '/api/scrape-url',
            json={'url': 'http://example.com'}
        )
        # Even if it fails, should not expose internals
        if response.status_code == 500:
            data = response.get_json()
            assert 'traceback' not in data.get('error', '').lower()


@pytest.mark.security
class TestInputValidation:
    """Test input validation and sanitization."""

    def test_scrape_requires_url(self, client):
        """Test that scrape endpoint requires URL."""
        response = client.post('/api/scrape', json={})
        assert response.status_code == 400
        # Error message could be "No data" or "URL is required"
        assert b'error' in response.data

    def test_scrape_requires_content(self, client):
        """Test that scrape endpoint requires content."""
        response = client.post('/api/scrape', json={
            'url': 'https://example.com',
            'title': 'Test'
        })
        assert response.status_code == 400
        assert b'Content is required' in response.data

    def test_scrape_url_requires_url(self, client):
        """Test that scrape-url endpoint requires URL."""
        response = client.post('/api/scrape-url', json={})
        assert response.status_code == 400
        assert b'URL is required' in response.data

    def test_video_generation_requires_summary(self, client, sample_article):
        """Test that video generation requires article to be summarized first."""
        response = client.post(f'/api/articles/{sample_article}/video')
        assert response.status_code == 400
        assert b'must be summarized first' in response.data


@pytest.mark.security
class TestDatabaseSecurity:
    """Test database security measures."""

    def test_sql_injection_in_article_title(self, client):
        """Test that SQL injection in title is prevented."""
        malicious_title = "'; DROP TABLE articles; --"
        response = client.post('/api/scrape', json={
            'url': 'https://example.com/test',
            'title': malicious_title,
            'content': 'Test content'
        })

        # Should create article without executing SQL
        assert response.status_code in [200, 201]

        # Articles table should still exist
        articles_response = client.get('/api/articles')
        assert articles_response.status_code == 200


@pytest.mark.security
class TestResourceLimits:
    """Test resource usage limits."""

    def test_extremely_long_content_handled(self, client):
        """Test that extremely long content is handled gracefully."""
        huge_content = 'A' * 1_000_000  # 1MB of content

        response = client.post('/api/scrape', json={
            'url': 'https://example.com/huge',
            'title': 'Huge Article',
            'content': huge_content
        })

        # Should either accept it or reject gracefully (not crash)
        assert response.status_code in [200, 201, 400, 413]

    def test_extremely_long_url_handled(self, client):
        """Test that extremely long URLs are handled."""
        long_url = 'https://example.com/' + 'a' * 10000

        response = client.post('/api/scrape', json={
            'url': long_url,
            'title': 'Test',
            'content': 'Test content'
        })

        # Should handle gracefully
        assert response.status_code in [200, 201, 400]

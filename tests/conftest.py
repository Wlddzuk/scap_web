"""Pytest fixtures for testing."""

import os
import sys
import tempfile
import pytest
from pathlib import Path

# Add parent directory to path so we can import app modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app as flask_app, db
from models import Article


@pytest.fixture
def app():
    """Create and configure a test Flask application."""
    # Create a temporary database
    db_fd, db_path = tempfile.mkstemp()

    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{db_path}',
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
    })

    # Create tables
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.drop_all()

    # Cleanup
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    """Create a test client for the app."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Create a test CLI runner."""
    return app.test_cli_runner()


@pytest.fixture
def sample_article(app):
    """Create a sample article in the database."""
    with app.app_context():
        article = Article(
            url='https://example.com/test-article',
            title='Test Article',
            content='This is a test article with some content for testing purposes.',
            site_name='Example Site',
            status='scraped'
        )
        db.session.add(article)
        db.session.commit()

        # Refresh to get the ID
        db.session.refresh(article)
        article_id = article.id

        yield article_id


@pytest.fixture
def summarized_article(app):
    """Create a summarized article with video script."""
    with app.app_context():
        article = Article(
            url='https://example.com/summarized-article',
            title='Summarized Article',
            content='This is a summarized article with AI-generated content.',
            tldr='This is a brief summary.',
            bullets='["Point 1", "Point 2", "Point 3"]',
            video_script='This is the video script. It contains the narrative for the video.',
            site_name='Example Site',
            status='summarized'
        )
        db.session.add(article)
        db.session.commit()

        db.session.refresh(article)
        article_id = article.id

        yield article_id


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Mock environment variables for testing."""
    monkeypatch.setenv('GEMINI_API_KEY', 'test_gemini_key')
    monkeypatch.setenv('GROQ_API_KEY', 'test_groq_key')
    monkeypatch.setenv('FAL_KEY', 'test_fal_key')
    monkeypatch.setenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5050')

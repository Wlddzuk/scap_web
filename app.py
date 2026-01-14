"""Flask API for Article Scraper MVP."""

import os
import json
import re
import logging
from datetime import datetime
from urllib.parse import urlparse
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

from models import db, Article
from summarizer import summarize_article
from video_generator import generate_video

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def scrape_url_content(url):
    """Fetch and parse article content from a URL server-side."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Remove unwanted elements
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
        tag.decompose()
    
    # Get title
    title = None
    if soup.find('h1'):
        title = soup.find('h1').get_text(strip=True)
    if not title:
        og_title = soup.find('meta', property='og:title')
        if og_title:
            title = og_title.get('content', '')
    if not title:
        title = soup.title.string if soup.title else 'Untitled'
    
    # Get site name
    site_name = urlparse(url).hostname
    og_site = soup.find('meta', property='og:site_name')
    if og_site:
        site_name = og_site.get('content', site_name)
    
    # Get hero image
    hero_image = None
    og_image = soup.find('meta', property='og:image')
    if og_image:
        hero_image = og_image.get('content')
    
    # Extract main content
    content = ''
    
    # Try to find article container
    article_el = soup.find('article')
    if not article_el:
        article_el = soup.find(class_=re.compile(r'article-body|post-content|entry-content|story-content|content-body'))
    if not article_el:
        article_el = soup.find(attrs={'itemprop': 'articleBody'})
    if not article_el:
        article_el = soup.find('main')
    if not article_el:
        article_el = soup.body
    
    if article_el:
        # Get all paragraph text
        paragraphs = article_el.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
        texts = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if len(text) > 20:
                texts.append(text)
        content = '\n\n'.join(texts)
    
    # Fallback to body text
    if not content or len(content) < 200:
        content = soup.body.get_text(separator=' ', strip=True)[:10000] if soup.body else ''
    
    return {
        'url': url,
        'title': title,
        'content': content,
        'hero_image': hero_image,
        'site_name': site_name
    }


# Initialize Flask app
app = Flask(__name__, static_folder='static')

# Configure CORS - whitelist specific origins for security
# In production, set CORS_ORIGINS env var to comma-separated list of allowed domains
allowed_origins = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5050').split(',')
CORS(app, resources={
    r"/api/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": False
    }
})

# Database configuration - use absolute path for reliability
db_path = os.getenv('DATABASE_URI', f'sqlite:///{os.path.abspath("instance/database.db")}')
app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Create tables on first run
with app.app_context():
    db.create_all()


# Validate API keys on startup
def validate_api_keys():
    """Check if at least one summarization API key is configured."""
    api_keys = {
        'OPENROUTER_API_KEY': os.getenv('OPENROUTER_API_KEY'),
        'GROQ_API_KEY': os.getenv('GROQ_API_KEY'),
        'MISTRAL_API_KEY': os.getenv('MISTRAL_API_KEY'),
        'GEMINI_API_KEY': os.getenv('GEMINI_API_KEY')
    }

    configured_keys = [name for name, value in api_keys.items() if value]

    if not configured_keys:
        logger.warning(
            "⚠️  No summarization API keys found! "
            "Set at least one of: OPENROUTER_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, or GEMINI_API_KEY"
        )
    else:
        logger.info(f"✓ Configured API keys: {', '.join(configured_keys)}")

    # FAL.ai key is optional (falls back to gradients)
    if os.getenv('FAL_KEY'):
        logger.info("✓ FAL.ai image generation enabled")
    else:
        logger.info("ℹ️  FAL_KEY not set - will use gradient backgrounds for videos")


validate_api_keys()


# ============================================================
# Static file serving (Dashboard)
# ============================================================

@app.route('/')
def serve_dashboard():
    """Serve the main dashboard."""
    return send_from_directory('static', 'index.html')


@app.route('/static/<path:filename>')
def serve_static(filename):
    """Serve static files."""
    return send_from_directory('static', filename)


@app.route('/videos/<path:filename>')
def serve_video(filename):
    """Serve generated videos with path sanitization."""
    # Sanitize filename to prevent directory traversal attacks
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return jsonify({'error': 'Invalid filename'}), 400
    return send_from_directory('static/videos', safe_filename)


# ============================================================
# API Endpoints
# ============================================================

@app.route('/api/scrape', methods=['POST'])
def scrape_article():
    """
    Receive scraped article content from the bookmarklet.
    
    Expected JSON:
    {
        "url": "https://example.com/article",
        "title": "Article Title",
        "content": "Full article text...",
        "hero_image": "https://example.com/image.jpg" (optional),
        "site_name": "Example Site" (optional)
    }
    """
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    url = data.get('url')
    title = data.get('title', 'Untitled')
    content = data.get('content', '')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    if not content:
        return jsonify({'error': 'Content is required'}), 400
    
    # Check if article already exists
    existing = Article.query.filter_by(url=url).first()
    if existing:
        return jsonify({
            'message': 'Article already exists',
            'article': existing.to_dict()
        }), 200
    
    # Create new article
    article = Article(
        url=url,
        title=title,
        content=content,
        hero_image=data.get('hero_image'),
        site_name=data.get('site_name'),
        status='scraped'
    )
    
    db.session.add(article)
    db.session.commit()
    
    return jsonify({
        'message': 'Article scraped successfully',
        'article': article.to_dict()
    }), 201


@app.route('/api/scrape-url', methods=['POST'])
def scrape_url():
    """
    Server-side URL scraping - fetches and parses article from URL.
    This bypasses browser CSP restrictions.
    
    Expected JSON:
    {
        "url": "https://example.com/article"
    }
    """
    data = request.get_json()
    
    if not data or not data.get('url'):
        return jsonify({'error': 'URL is required'}), 400
    
    url = data.get('url')
    
    # Check if article already exists
    existing = Article.query.filter_by(url=url).first()
    if existing:
        return jsonify({
            'message': 'Article already exists',
            'article': existing.to_dict()
        }), 200
    
    try:
        # Fetch and parse the article server-side
        scraped = scrape_url_content(url)
        
        if not scraped['content'] or len(scraped['content']) < 100:
            return jsonify({'error': 'Could not extract article content from URL'}), 400
        
        # Create new article
        article = Article(
            url=scraped['url'],
            title=scraped['title'],
            content=scraped['content'],
            hero_image=scraped['hero_image'],
            site_name=scraped['site_name'],
            status='scraped'
        )
        
        db.session.add(article)
        db.session.commit()
        
        return jsonify({
            'message': 'Article scraped successfully',
            'article': article.to_dict()
        }), 201
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {str(e)}")
        return jsonify({'error': 'Failed to fetch the URL. Please check the URL and try again.'}), 400
    except Exception as e:
        logger.error(f"Failed to parse article from {url}: {str(e)}", exc_info=True)
        return jsonify({'error': 'Failed to parse article content. The page format may not be supported.'}), 500


@app.route('/api/articles', methods=['GET'])
def list_articles():
    """List all scraped articles, newest first."""
    articles = Article.query.order_by(Article.scraped_at.desc()).all()
    return jsonify({
        'articles': [a.to_dict() for a in articles],
        'count': len(articles)
    })


@app.route('/api/articles/<int:article_id>', methods=['GET'])
def get_article(article_id):
    """Get a single article by ID."""
    article = Article.query.get_or_404(article_id)
    return jsonify(article.to_dict())


@app.route('/api/articles/<int:article_id>', methods=['DELETE'])
def delete_article(article_id):
    """Delete an article."""
    article = Article.query.get_or_404(article_id)
    db.session.delete(article)
    db.session.commit()
    return jsonify({'message': 'Article deleted'})


@app.route('/api/articles/<int:article_id>/summarize', methods=['POST'])
def summarize_article_endpoint(article_id):
    """Trigger AI summarization for an article."""
    article = Article.query.get_or_404(article_id)
    
    # Update status
    article.status = 'summarizing'
    db.session.commit()
    
    try:
        # Call Gemini summarizer
        result = summarize_article(article.title, article.content)
        
        # Update article with summary
        article.tldr = result['tldr']
        article.bullets = json.dumps(result['bullets'])
        article.video_script = result['video_script']
        article.hashtags = json.dumps(result.get('hashtags', []))
        article.status = 'summarized'
        article.summarized_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Article summarized successfully',
            'article': article.to_dict()
        })

    except Exception as e:
        logger.error(f"Failed to summarize article {article_id}: {str(e)}", exc_info=True)
        article.status = 'failed'
        db.session.commit()
        return jsonify({'error': 'Failed to generate summary. Please try again later.'}), 500


@app.route('/api/articles/<int:article_id>/video', methods=['POST'])
def generate_video_endpoint(article_id):
    """Trigger video generation for an article."""
    article = Article.query.get_or_404(article_id)
    
    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400
    
    # Update status
    article.status = 'generating_video'
    db.session.commit()
    
    try:
        # Generate video
        video_path = generate_video(
            article_id=article.id,
            title=article.title,
            script=article.video_script,
            hero_image=article.hero_image
        )
        
        # Store relative path for serving
        relative_path = os.path.basename(video_path)
        
        article.video_path = relative_path
        article.status = 'video_done'
        article.video_generated_at = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'message': 'Video generated successfully',
            'article': article.to_dict(),
            'video_url': f'/videos/{relative_path}'
        })

    except Exception as e:
        logger.error(f"Failed to generate video for article {article_id}: {str(e)}", exc_info=True)
        article.status = 'failed'
        db.session.commit()
        return jsonify({'error': 'Failed to generate video. Please try again later.'}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat()
    })


# ============================================================
# Run Server
# ============================================================

if __name__ == '__main__':
    print("\n" + "="*60)
    print("  🚀 Article Scraper MVP")
    print("="*60)
    print("\n  Dashboard: http://localhost:5050")
    print("  API Base:  http://localhost:5050/api")
    print("\n" + "="*60 + "\n")
    
    app.run(host='0.0.0.0', port=5050, debug=True)

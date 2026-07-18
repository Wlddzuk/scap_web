"""Flask API for Clipper - Article to TikTok Video Generator."""

import os
import json
import re
import logging
import ipaddress
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse
from threading import Thread
import shutil
import zipfile
from io import BytesIO
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

from models import db, Article
from summarizer import summarize_article
from video_generator import generate_video
from carousel_generator import generate_carousel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# URL Validation (SSRF prevention)
# ============================================================

BLOCKED_NETWORKS = [
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('0.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


def validate_url(url: str) -> str:
    """Validate and sanitize URL, preventing SSRF attacks.

    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)

    if parsed.scheme not in ('http', 'https'):
        raise ValueError('Only HTTP and HTTPS URLs are allowed')

    if not parsed.hostname:
        raise ValueError('Invalid URL: no hostname')

    hostname = parsed.hostname.lower()

    # Block obviously internal hostnames
    if hostname in ('localhost', 'metadata.google.internal', 'metadata'):
        raise ValueError('Internal URLs are not allowed')

    # Resolve hostname and check against blocked networks
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            for network in BLOCKED_NETWORKS:
                if ip in network:
                    raise ValueError('URLs pointing to internal networks are not allowed')
    except socket.gaierror:
        raise ValueError('Could not resolve hostname')

    return url


def scrape_url_content(url):
    """Fetch and parse article content from a URL server-side."""
    url = validate_url(url)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }

    response = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
    response.raise_for_status()

    # Validate final URL after redirects
    if response.url != url:
        validate_url(response.url)

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

# Configure CORS
allowed_origins = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5050').split(',')
CORS(app, resources={
    r"/api/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "supports_credentials": False
    }
})

# Database configuration
db_path = os.getenv('DATABASE_URI', f'sqlite:///{os.path.abspath("instance/database.db")}')
app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

with app.app_context():
    db.create_all()

    # Auto-migrate: add carousel columns if missing (for existing databases)
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    existing_columns = [c['name'] for c in inspector.get_columns('articles')]
    migrations = {
        'carousel_dir': 'VARCHAR(512)',
        'carousel_audio': 'VARCHAR(512)',
        'carousel_generated_at': 'DATETIME',
    }
    for col_name, col_type in migrations.items():
        if col_name not in existing_columns:
            db.session.execute(text(f'ALTER TABLE articles ADD COLUMN {col_name} {col_type}'))
            logger.info(f"Migrated: added column '{col_name}' to articles table")
    db.session.commit()

    # Auto-migrate: add discovery scoring to databases created before story discovery.
    inspector = inspect(db.engine)
    existing_columns = [c['name'] for c in inspector.get_columns('articles')]
    discovery_migrations = {
        'viral_score': 'FLOAT',
    }
    for col_name, col_type in discovery_migrations.items():
        if col_name not in existing_columns:
            db.session.execute(text(f'ALTER TABLE articles ADD COLUMN {col_name} {col_type}'))
            logger.info(f"Migrated: added column '{col_name}' to articles table")
    db.session.commit()


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
            "No summarization API keys found! "
            "Set at least one of: OPENROUTER_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, or GEMINI_API_KEY"
        )
    else:
        logger.info(f"Configured API keys: {', '.join(configured_keys)}")

    if os.getenv('FAL_KEY'):
        logger.info("FAL.ai image generation enabled")
    else:
        logger.info("FAL_KEY not set - will use gradient backgrounds for videos")


validate_api_keys()


# ============================================================
# Background task helpers
# ============================================================

def run_summarize_in_background(app_context, article_id):
    """Run summarization in a background thread."""
    with app_context:
        article = db.session.get(Article, article_id)
        if not article:
            return

        try:
            result = summarize_article(article.title, article.content)

            article.tldr = result['tldr']
            article.bullets = json.dumps(result['bullets'])
            article.video_script = result['video_script']
            article.hashtags = json.dumps(result.get('hashtags', []))
            article.status = 'summarized'
            article.summarized_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"Article {article_id} summarized successfully")

        except Exception as e:
            logger.error(f"Failed to summarize article {article_id}: {e}", exc_info=True)
            article.status = 'failed'
            db.session.commit()


def run_video_in_background(app_context, article_id, image_source="ai"):
    """Run video generation in a background thread."""
    with app_context:
        article = db.session.get(Article, article_id)
        if not article:
            return

        try:
            video_path = generate_video(
                article_id=article.id,
                title=article.title,
                script=article.video_script,
                image_source=image_source
            )

            relative_path = os.path.basename(video_path)
            article.video_path = relative_path
            article.status = 'video_done'
            article.video_generated_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"Video generated for article {article_id}")

        except Exception as e:
            logger.error(f"Failed to generate video for article {article_id}: {e}", exc_info=True)
            article.status = 'failed'
            db.session.commit()


def run_carousel_in_background(app_context, article_id, image_source="ai"):
    """Run carousel generation in a background thread."""
    with app_context:
        article = db.session.get(Article, article_id)
        if not article:
            return

        try:
            result = generate_carousel(
                article_id=article.id,
                title=article.title,
                script=article.video_script,
                image_source=image_source
            )

            article.carousel_dir = result['carousel_dir']
            article.carousel_audio = result['carousel_audio']
            article.status = 'carousel_done'
            article.carousel_generated_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(f"Carousel generated for article {article_id}")

        except Exception as e:
            logger.error(f"Failed to generate carousel for article {article_id}: {e}", exc_info=True)
            article.status = 'failed'
            db.session.commit()


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
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return jsonify({'error': 'Invalid filename'}), 400
    return send_from_directory('static/videos', safe_filename)


@app.route('/carousels/<int:article_id>/<path:filename>')
def serve_carousel_file(article_id, filename):
    """Serve carousel images and audio files."""
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return jsonify({'error': 'Invalid filename'}), 400
    carousel_dir = os.path.join('static', 'carousels', str(article_id))
    if not os.path.isdir(carousel_dir):
        return jsonify({'error': 'Carousel not found'}), 404
    return send_from_directory(carousel_dir, safe_filename)


@app.route('/api/articles/<int:article_id>/carousel/download')
def download_carousel_zip(article_id):
    """Download all carousel assets as a ZIP file."""
    article = db.session.get(Article, article_id)
    if not article or not article.carousel_dir:
        return jsonify({'error': 'Carousel not found'}), 404

    carousel_path = os.path.join('static', 'carousels', article.carousel_dir)
    if not os.path.isdir(carousel_path):
        return jsonify({'error': 'Carousel files not found'}), 404

    # Create ZIP in memory
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in sorted(os.listdir(carousel_path)):
            fpath = os.path.join(carousel_path, fname)
            if os.path.isfile(fpath):
                zf.write(fpath, fname)

    zip_buffer.seek(0)

    # Clean title for filename
    safe_title = re.sub(r'[^\w\s-]', '', article.title)[:40].strip().replace(' ', '_')
    zip_name = f"carousel_{safe_title}_{article_id}.zip"

    return send_file(
        zip_buffer,
        mimetype='application/zip',
        as_attachment=True,
        download_name=zip_name
    )


@app.route('/api/articles/<int:article_id>/carousel/qr')
def carousel_qr_code(article_id):
    """Generate a QR code pointing to the mobile download page."""
    import qrcode

    article = db.session.get(Article, article_id)
    if not article or not article.carousel_dir:
        return jsonify({'error': 'Carousel not found'}), 404

    # Get the local network IP so the phone can access it
    local_ip = _get_local_ip()
    port = request.host.split(':')[-1] if ':' in request.host else '5050'
    mobile_url = f"http://{local_ip}:{port}/carousels/{article_id}/mobile"

    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(mobile_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to PNG bytes
    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(buf, mimetype='image/png')


def _get_local_ip():
    """Get the local network IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


@app.route('/carousels/<int:article_id>/mobile')
def carousel_mobile_page(article_id):
    """Serve a mobile-friendly page to save carousel images to Camera Roll."""
    article = db.session.get(Article, article_id)
    if not article or not article.carousel_dir:
        return "Carousel not found", 404

    carousel_path = os.path.join('static', 'carousels', article.carousel_dir)
    if not os.path.isdir(carousel_path):
        return "Carousel files not found", 404

    # Get list of slide files
    slides = sorted([f for f in os.listdir(carousel_path) if f.startswith('slide_') and f.endswith('.png')])
    audio_file = article.carousel_audio

    # Build a self-contained mobile HTML page
    slides_html = ""
    for i, slide in enumerate(slides, 1):
        slides_html += f'''
        <div class="slide-card">
            <div class="slide-number">Slide {i}</div>
            <img src="/carousels/{article_id}/{slide}" alt="Slide {i}" class="slide-img">
            <a href="/carousels/{article_id}/{slide}" download="{slide}" class="save-btn">
                💾 Save Image {i}
            </a>
        </div>
        '''

    audio_html = ""
    if audio_file:
        audio_html = f'''
        <div class="audio-card">
            <div class="slide-number">🎙️ Voiceover</div>
            <audio controls preload="metadata" class="audio-player">
                <source src="/carousels/{article_id}/{audio_file}">
            </audio>
            <a href="/carousels/{article_id}/{audio_file}" download="{audio_file}" class="save-btn">
                💾 Save Audio
            </a>
        </div>
        '''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Clipper — Save Carousel</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: #0B0F14;
            color: #F3F4F6;
            min-height: 100vh;
            padding: 20px;
            padding-bottom: 40px;
            -webkit-font-smoothing: antialiased;
        }}
        .header {{
            text-align: center;
            padding: 20px 0 24px;
        }}
        .logo {{ color: #5EEAD4; font-size: 0.9rem; }}
        h1 {{
            font-size: 1.5rem;
            font-weight: 700;
            margin: 8px 0 4px;
            letter-spacing: -0.02em;
        }}
        .subtitle {{
            color: #9CA3AF;
            font-size: 0.85rem;
            line-height: 1.4;
        }}
        .tip {{
            background: rgba(94, 234, 212, 0.1);
            border: 1px solid rgba(94, 234, 212, 0.2);
            border-radius: 12px;
            padding: 12px 16px;
            margin: 16px 0 20px;
            font-size: 0.8rem;
            color: #5EEAD4;
            text-align: center;
        }}
        .slide-card {{
            background: #111827;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            overflow: hidden;
            margin-bottom: 16px;
        }}
        .slide-number {{
            padding: 12px 16px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #9CA3AF;
        }}
        .slide-img {{
            width: 100%;
            display: block;
            border-top: 1px solid rgba(255,255,255,0.06);
            border-bottom: 1px solid rgba(255,255,255,0.06);
        }}
        .save-btn {{
            display: block;
            text-align: center;
            padding: 14px;
            color: #0B0F14;
            background: #5EEAD4;
            font-weight: 600;
            font-size: 0.9rem;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .save-btn:active {{ background: #3dd1b9; }}
        .audio-card {{
            background: #111827;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            overflow: hidden;
            margin-bottom: 16px;
        }}
        .audio-player {{
            width: calc(100% - 32px);
            margin: 0 16px 12px;
            height: 44px;
        }}
        .instructions {{
            text-align: center;
            padding: 20px 0;
            color: #667085;
            font-size: 0.75rem;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">▲ Clipper</div>
        <h1>{article.title[:60]}</h1>
        <p class="subtitle">Photo Carousel — {len(slides)} slides</p>
    </div>
    <div class="tip">
        📱 <strong>Tip:</strong> Long-press each image → "Save to Photos"<br>
        Or tap the save buttons below each slide
    </div>
    {slides_html}
    {audio_html}
    <div class="instructions">
        After saving, open TikTok → Create → Photo Mode<br>
        Select all images from Camera Roll → Add voiceover
    </div>
</body>
</html>'''

    return html


@app.route('/api/articles/<int:article_id>/video/qr')
def video_qr_code(article_id):
    """Generate a QR code pointing to the mobile video download page."""
    import qrcode

    article = db.session.get(Article, article_id)
    if not article or not article.video_path:
        return jsonify({'error': 'Video not found'}), 404

    local_ip = _get_local_ip()
    port = request.host.split(':')[-1] if ':' in request.host else '5050'
    mobile_url = f"http://{local_ip}:{port}/videos/{article_id}/mobile"

    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(mobile_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(buf, mimetype='image/png')


@app.route('/videos/<int:article_id>/mobile')
def video_mobile_page(article_id):
    """Serve a mobile-friendly page to save a video to Camera Roll."""
    article = db.session.get(Article, article_id)
    if not article or not article.video_path:
        return "Video not found", 404

    video_url = f"/videos/{article.video_path}"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>Clipper — Save Video</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, sans-serif;
            background: #0B0F14;
            color: #F3F4F6;
            min-height: 100vh;
            padding: 20px;
            padding-bottom: 40px;
            -webkit-font-smoothing: antialiased;
        }}
        .header {{
            text-align: center;
            padding: 20px 0 24px;
        }}
        .logo {{ color: #5EEAD4; font-size: 0.9rem; }}
        h1 {{
            font-size: 1.4rem;
            font-weight: 700;
            margin: 8px 0 4px;
            letter-spacing: -0.02em;
        }}
        .subtitle {{
            color: #9CA3AF;
            font-size: 0.85rem;
        }}
        .tip {{
            background: rgba(94, 234, 212, 0.1);
            border: 1px solid rgba(94, 234, 212, 0.2);
            border-radius: 12px;
            padding: 12px 16px;
            margin: 16px 0 20px;
            font-size: 0.8rem;
            color: #5EEAD4;
            text-align: center;
        }}
        .video-card {{
            background: #111827;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 16px;
            overflow: hidden;
            margin-bottom: 16px;
        }}
        .video-card video {{
            width: 100%;
            display: block;
        }}
        .save-btn {{
            display: block;
            text-align: center;
            padding: 16px;
            color: #0B0F14;
            background: #5EEAD4;
            font-weight: 600;
            font-size: 1rem;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .save-btn:active {{ background: #3dd1b9; }}
        .instructions {{
            text-align: center;
            padding: 20px 0;
            color: #667085;
            font-size: 0.75rem;
            line-height: 1.6;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">▲ Clipper</div>
        <h1>{article.title[:60]}</h1>
        <p class="subtitle">Generated Video</p>
    </div>
    <div class="tip">
        📱 <strong>Tip:</strong> Tap "Save Video" or long-press the video → "Save to Photos"
    </div>
    <div class="video-card">
        <video controls playsinline preload="metadata">
            <source src="{video_url}" type="video/mp4">
        </video>
        <a href="{video_url}" download="{article.video_path}" class="save-btn">
            💾 Save Video
        </a>
    </div>
    <div class="instructions">
        After saving, open TikTok → Create → Upload<br>
        Select the video from Camera Roll
    </div>
</body>
</html>'''

    return html


# ============================================================
# API Endpoints
# ============================================================

@app.route('/api/scrape', methods=['POST'])
def scrape_article():
    """Receive scraped article content from the bookmarklet."""
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
    """Server-side URL scraping - fetches and parses article from URL."""
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
        scraped = scrape_url_content(url)

        if not scraped['content'] or len(scraped['content']) < 100:
            return jsonify({'error': 'Could not extract article content from URL'}), 400

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

    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch URL {url}: {e}")
        return jsonify({'error': 'Failed to fetch the URL. Please check the URL and try again.'}), 400
    except Exception as e:
        logger.error(f"Failed to parse article from {url}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to parse article content. The page format may not be supported.'}), 500


@app.route('/api/articles', methods=['GET'])
def list_articles():
    """List all scraped articles, newest first."""
    articles = Article.query.order_by(Article.scraped_at.desc()).all()
    return jsonify({
        'articles': [a.to_dict(include_full_content=False) for a in articles],
        'count': len(articles)
    })


@app.route('/api/articles/<int:article_id>', methods=['GET'])
def get_article(article_id):
    """Get a single article by ID."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404
    return jsonify(article.to_dict(include_full_content=True))


@app.route('/api/articles/<int:article_id>', methods=['DELETE'])
def delete_article(article_id):
    """Delete an article and its generated video."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    # Clean up video file if it exists
    if article.video_path:
        video_file = os.path.join('static', 'videos', article.video_path)
        if os.path.exists(video_file):
            try:
                os.remove(video_file)
            except OSError as e:
                logger.warning(f"Could not delete video file {video_file}: {e}")

    # Clean up carousel directory if it exists
    if article.carousel_dir:
        carousel_path = os.path.join('static', 'carousels', article.carousel_dir)
        if os.path.isdir(carousel_path):
            try:
                shutil.rmtree(carousel_path)
            except OSError as e:
                logger.warning(f"Could not delete carousel dir {carousel_path}: {e}")

    db.session.delete(article)
    db.session.commit()
    return jsonify({'message': 'Article deleted'})


@app.route('/api/articles/<int:article_id>/summarize', methods=['POST'])
def summarize_article_endpoint(article_id):
    """Trigger AI summarization for an article (runs in background)."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if article.status in ('summarizing', 'generating_video', 'generating_carousel'):
        return jsonify({'error': 'Article is already being processed'}), 409

    article.status = 'summarizing'
    db.session.commit()

    # Run in background thread so the request returns immediately
    thread = Thread(
        target=run_summarize_in_background,
        args=(app.app_context(), article.id)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'message': 'Summarization started',
        'article': article.to_dict()
    }), 202


@app.route('/api/articles/<int:article_id>/video', methods=['POST'])
def generate_video_endpoint(article_id):
    """Trigger video generation for an article (runs in background)."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400

    if article.status in ('summarizing', 'generating_video', 'generating_carousel'):
        return jsonify({'error': 'Article is already being processed'}), 409

    # Get image source from request body
    data = request.get_json(silent=True) or {}
    image_source = data.get('image_source', 'ai')
    if image_source not in ('ai', 'stock'):
        image_source = 'ai'

    article.status = 'generating_video'
    db.session.commit()

    # Run in background thread
    thread = Thread(
        target=run_video_in_background,
        args=(app.app_context(), article.id, image_source)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'message': 'Video generation started',
        'article': article.to_dict()
    }), 202


@app.route('/api/articles/<int:article_id>/carousel', methods=['POST'])
def generate_carousel_endpoint(article_id):
    """Trigger carousel generation for an article (runs in background)."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400

    if article.status in ('summarizing', 'generating_video', 'generating_carousel'):
        return jsonify({'error': 'Article is already being processed'}), 409

    # Get image source from request body
    data = request.get_json(silent=True) or {}
    image_source = data.get('image_source', 'ai')
    if image_source not in ('ai', 'stock'):
        image_source = 'ai'

    article.status = 'generating_carousel'
    db.session.commit()

    # Run in background thread
    thread = Thread(
        target=run_carousel_in_background,
        args=(app.app_context(), article.id, image_source)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'message': 'Carousel generation started',
        'article': article.to_dict()
    }), 202


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


# ============================================================
# Run Server
# ============================================================

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  Clipper - Article to TikTok Video Generator")
    print("=" * 60)
    print("\n  Dashboard: http://localhost:5050")
    print("  API Base:  http://localhost:5050/api")
    print("\n" + "=" * 60 + "\n")

    app.run(host='0.0.0.0', port=5050, debug=True)

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
from visual_styles import list_styles, get_style, STYLES as VISUAL_STYLES

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


def _migrate_schema():
    """Idempotent SQLite migration: add new columns if they don't exist yet."""
    from sqlalchemy import text, inspect

    new_cols = [
        ("scenes", "TEXT"),
        ("hook_variants", "TEXT"),
        ("dominant_emotion", "VARCHAR(32)"),
        ("style", "VARCHAR(32)"),
        ("substack_post", "TEXT"),
    ]

    with app.app_context():
        inspector = inspect(db.engine)
        if "articles" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("articles")}
        with db.engine.begin() as conn:
            for col_name, col_type in new_cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}"))
                    logger.info(f"Schema migrated: added articles.{col_name}")


with app.app_context():
    db.create_all()

_migrate_schema()


def _prune_missing_videos():
    """Null out video_path for articles whose mp4 file no longer exists on disk.

    SQLite rows can outlive their referenced files (manual deletes, static
    dir reset, branch switches). Stale video_path values cause the frontend
    to render <video> tags pointing at 404s, which pollutes the network tab
    and burns bytes on every page load. One-shot cleanup at startup keeps
    the dashboard honest.
    """
    videos_dir = os.path.join(os.path.dirname(__file__), "static", "videos")
    with app.app_context():
        stale = Article.query.filter(Article.video_path.isnot(None)).all()
        pruned = 0
        for a in stale:
            full = os.path.join(videos_dir, a.video_path)
            if not os.path.exists(full):
                a.video_path = None
                # If the only reason we called this 'video_done' was that stale
                # path, walk back to a sensible state.
                if a.status == 'video_done':
                    a.status = 'summarized' if a.tldr else 'scraped'
                pruned += 1
        if pruned:
            db.session.commit()
            logger.info(f"Pruned {pruned} stale video_path entries on startup")


_prune_missing_videos()


# Validate API keys on startup
def validate_api_keys():
    """Check if at least one summarization API key is configured."""
    api_keys = {
        'OPENROUTER_API_KEY': os.getenv('OPENROUTER_API_KEY'),
        'GROQ_API_KEY': os.getenv('GROQ_API_KEY'),
        'GEMINI_API_KEY': os.getenv('GEMINI_API_KEY')
    }

    configured_keys = [name for name, value in api_keys.items() if value]

    if not configured_keys:
        logger.warning(
            "No summarization API keys found! "
            "Set at least one of: OPENROUTER_API_KEY, GROQ_API_KEY, or GEMINI_API_KEY"
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
#
# Note on pre-warming: we deliberately do NOT pre-import kokoro on startup.
# If that import ever hangs (torch dispatch init on cold machines), the hung
# import holds Python's import lock — which means the first real video
# request will ALSO block on `from kokoro import KPipeline`, forever, waiting
# for the import lock that the hung warmup thread is holding. Pre-warm helps
# only when it finishes; when it doesn't, it breaks every subsequent request.
# The watchdog below is the right defense: it gives users a clean terminal
# 'failed' state instead of a UI that spins forever.

# Max wall-time for a single video generation before the watchdog flips the
# article to 'failed'. Configurable via env. The worker thread may still be
# running after this — Python can't safely kill threads — but the UI sees a
# clean terminal state so users can retry.
VIDEO_TIMEOUT_SECONDS = int(os.getenv("VIDEO_TIMEOUT_SECONDS", "900"))


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

            # Engagement metadata
            scenes = result.get('scenes') or []
            article.scenes = json.dumps(scenes) if scenes else None
            hook_variants = result.get('hook_variants') or []
            article.hook_variants = json.dumps(hook_variants) if hook_variants else None
            article.dominant_emotion = result.get('dominant_emotion') or None
            suggested = result.get('suggested_style')
            if suggested and suggested in VISUAL_STYLES:
                article.style = suggested

            article.status = 'summarized'
            article.summarized_at = datetime.now(timezone.utc)
            db.session.commit()
            logger.info(
                f"Article {article_id} summarized (scenes={len(scenes)}, "
                f"style={article.style}, emotion={article.dominant_emotion})"
            )

        except Exception as e:
            logger.error(f"Failed to summarize article {article_id}: {e}", exc_info=True)
            article.status = 'failed'
            db.session.commit()


def run_video_in_background(app_context, article_id, style_override=None):
    """Run video generation in a background thread, with a watchdog timeout.

    If generation exceeds VIDEO_TIMEOUT_SECONDS, a separate timer thread flips
    the article to 'failed' so the UI shows a clean terminal state. The worker
    thread itself may keep running (Python can't safely kill threads), so on
    successful completion we re-check status and discard the output if the
    watchdog already declared failure.
    """
    from threading import Timer

    def _watchdog_fire():
        # Runs in a separate thread — needs its own app context.
        with app.app_context():
            article = db.session.get(Article, article_id)
            if article and article.status == 'generating_video':
                logger.error(
                    f"Video generation for article {article_id} timed out after "
                    f"{VIDEO_TIMEOUT_SECONDS}s. Marking failed. Worker thread may "
                    "still be running and will discard its output on completion."
                )
                article.status = 'failed'
                db.session.commit()

    timer = Timer(VIDEO_TIMEOUT_SECONDS, _watchdog_fire)
    timer.daemon = True
    timer.start()

    try:
        with app_context:
            article = db.session.get(Article, article_id)
            if not article:
                return

            try:
                scenes = json.loads(article.scenes) if article.scenes else None
                style_key = style_override or article.style or None

                video_path = generate_video(
                    article_id=article.id,
                    title=article.title,
                    script=article.video_script,
                    scenes=scenes,
                    style_key=style_key,
                    emotion=article.dominant_emotion,
                )

                # Re-fetch: watchdog may have already marked us failed while we
                # were inside generate_video(). If so, drop the result so we
                # don't revive a failed row.
                db.session.refresh(article)
                if article.status != 'generating_video':
                    logger.warning(
                        f"Video for article {article_id} completed after watchdog "
                        f"already set status={article.status}; discarding {video_path}"
                    )
                    try:
                        os.remove(video_path)
                    except OSError:
                        pass
                    return

                # Persist the style that was actually used (in case it was auto-picked inside)
                if style_override:
                    article.style = style_override

                relative_path = os.path.basename(video_path)
                article.video_path = relative_path
                article.status = 'video_done'
                article.video_generated_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(f"Video generated for article {article_id}")

            except Exception as e:
                logger.error(f"Failed to generate video for article {article_id}: {e}", exc_info=True)
                # Only overwrite status if watchdog hasn't already set it.
                db.session.refresh(article)
                if article.status == 'generating_video':
                    article.status = 'failed'
                    db.session.commit()
    finally:
        timer.cancel()


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

    db.session.delete(article)
    db.session.commit()
    return jsonify({'message': 'Article deleted'})


@app.route('/api/articles/<int:article_id>/summarize', methods=['POST'])
def summarize_article_endpoint(article_id):
    """Trigger AI summarization for an article (runs in background)."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if article.status in ('summarizing', 'generating_video'):
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
    """Trigger video generation for an article (runs in background).

    Optional JSON body: {"style": "manga"} overrides the auto-picked style.
    """
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400

    if article.status in ('summarizing', 'generating_video'):
        return jsonify({'error': 'Article is already being processed'}), 409

    payload = request.get_json(silent=True) or {}
    style_override = payload.get('style')
    if style_override and style_override not in VISUAL_STYLES:
        return jsonify({'error': f'Unknown style: {style_override}'}), 400

    article.status = 'generating_video'
    db.session.commit()

    thread = Thread(
        target=run_video_in_background,
        args=(app.app_context(), article.id, style_override)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'message': 'Video generation started',
        'article': article.to_dict()
    }), 202


@app.route('/api/articles/<int:article_id>/substack', methods=['POST'])
def generate_substack_endpoint(article_id):
    """Generate (or return cached) Substack companion post for an article.

    Synchronous — the LLM call takes ~5-10s, far short of request timeout.
    Returns the updated article dict on success (200).
    """
    from summarizer import generate_substack_post

    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.tldr:
        return jsonify({'error': 'Article must be summarized first'}), 400

    # Return cached post if already generated
    if article.substack_post:
        return jsonify({'article': article.to_dict()})

    try:
        post = generate_substack_post(article)
        article.substack_post = post
        db.session.commit()
        return jsonify({'article': article.to_dict()})
    except Exception as e:
        logger.error("Substack post generation failed for article %s: %s", article_id, e, exc_info=True)
        return jsonify({'error': 'Failed to generate Substack post'}), 500


@app.route('/api/styles', methods=['GET'])
def list_styles_endpoint():
    """Return available visual style presets for UI consumption."""
    return jsonify({'styles': list_styles()})


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

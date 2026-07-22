"""Flask API for Clipper - Article to TikTok Video Generator."""

import os
import json
import re
import logging
import ipaddress
import socket
import secrets
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlencode
from threading import Thread
import shutil
import zipfile
from io import BytesIO
from flask import Flask, request, jsonify, send_from_directory, send_file, session, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup

load_dotenv()

from models import db, Article, TikTokAccount
from summarizer import summarize_article
from video_generator import generate_video
from carousel_generator import generate_carousel
from visual_styles import list_styles, get_style, STYLES as VISUAL_STYLES
from tiktok_service import (
    AUTH_URL as TIKTOK_AUTH_URL,
    TikTokAPIError,
    TokenCipher,
    exchange_code as tiktok_exchange_code,
    refresh_access_token as tiktok_refresh_access_token,
    revoke_access_token as tiktok_revoke_access_token,
    query_creator_info as tiktok_query_creator_info,
    make_upload_plan,
    initialize_video_post,
    upload_video_file,
    fetch_publish_status,
)

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

# OAuth state is kept in Flask's signed session cookie. Production must set a
# stable secret so callbacks remain valid across process restarts/workers.
_configured_flask_secret = os.getenv('FLASK_SECRET_KEY')
app.secret_key = _configured_flask_secret or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', 'false').lower() == 'true',
)
if not _configured_flask_secret:
    logger.warning('FLASK_SECRET_KEY is not set; TikTok OAuth is disabled until configured')

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
        ("carousel_dir", "VARCHAR(512)"),
        ("carousel_audio", "VARCHAR(512)"),
        ("carousel_generated_at", "DATETIME"),
        ("viral_score", "FLOAT"),
        ("tiktok_publish_id", "VARCHAR(256)"),
        ("tiktok_publish_status", "VARCHAR(64)"),
        ("tiktok_publish_error", "TEXT"),
        ("tiktok_published_at", "DATETIME"),
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
# TikTok connection helpers
# ============================================================

TIKTOK_SCOPES = ('user.info.basic', 'video.publish')
TIKTOK_PENDING_STATUSES = {
    'INITIALIZING',
    'UPLOADING',
    'PROCESSING_UPLOAD',
    'PROCESSING_DOWNLOAD',
}


def _tiktok_config():
    return {
        'client_key': os.getenv('TIKTOK_CLIENT_KEY', '').strip(),
        'client_secret': os.getenv('TIKTOK_CLIENT_SECRET', '').strip(),
        'redirect_uri': os.getenv('TIKTOK_REDIRECT_URI', '').strip(),
        'encryption_secret': (
            os.getenv('TIKTOK_TOKEN_ENCRYPTION_KEY', '').strip()
            or (_configured_flask_secret or '')
        ),
    }


def _tiktok_missing_config():
    config = _tiktok_config()
    missing = []
    if not config['client_key']:
        missing.append('TIKTOK_CLIENT_KEY')
    if not config['client_secret']:
        missing.append('TIKTOK_CLIENT_SECRET')
    if not config['redirect_uri']:
        missing.append('TIKTOK_REDIRECT_URI')
    if not _configured_flask_secret:
        missing.append('FLASK_SECRET_KEY')
    if not config['encryption_secret']:
        missing.append('TIKTOK_TOKEN_ENCRYPTION_KEY')
    return missing


def _tiktok_cipher():
    return TokenCipher(_tiktok_config()['encryption_secret'])


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _store_tiktok_tokens(token_data):
    required = ('open_id', 'access_token', 'refresh_token', 'expires_in', 'refresh_expires_in')
    missing = [key for key in required if token_data.get(key) in (None, '')]
    if missing:
        raise TikTokAPIError(
            f"TikTok token response omitted: {', '.join(missing)}",
            code='invalid_token_response',
        )

    cipher = _tiktok_cipher()
    now = datetime.now(timezone.utc)
    account = TikTokAccount.query.order_by(TikTokAccount.id.asc()).first()
    if account is None:
        account = TikTokAccount(open_id=token_data['open_id'])
        db.session.add(account)

    account.open_id = token_data['open_id']
    account.access_token_encrypted = cipher.encrypt(token_data['access_token'])
    account.refresh_token_encrypted = cipher.encrypt(token_data['refresh_token'])
    account.scope = token_data.get('scope') or account.scope
    account.access_token_expires_at = now + timedelta(seconds=int(token_data['expires_in']))
    account.refresh_token_expires_at = now + timedelta(seconds=int(token_data['refresh_expires_in']))
    account.updated_at = now
    db.session.commit()
    return account


def _connected_tiktok_account():
    return TikTokAccount.query.order_by(TikTokAccount.id.asc()).first()


def _tiktok_access_token():
    """Return a valid access token, refreshing it five minutes before expiry."""
    account = _connected_tiktok_account()
    if account is None:
        raise TikTokAPIError('Connect a TikTok account first', code='not_connected')

    config = _tiktok_config()
    cipher = _tiktok_cipher()
    now = datetime.now(timezone.utc)
    expiry = _as_utc(account.access_token_expires_at)
    if expiry and expiry > now + timedelta(minutes=5):
        return cipher.decrypt(account.access_token_encrypted), account

    refresh_expiry = _as_utc(account.refresh_token_expires_at)
    if refresh_expiry and refresh_expiry <= now:
        raise TikTokAPIError('TikTok connection expired; reconnect the account', code='refresh_expired')

    refreshed = tiktok_refresh_access_token(
        client_key=config['client_key'],
        client_secret=config['client_secret'],
        refresh_token=cipher.decrypt(account.refresh_token_encrypted),
    )
    account = _store_tiktok_tokens(refreshed)
    return _tiktok_cipher().decrypt(account.access_token_encrypted), account


def _refresh_creator_info():
    access_token, account = _tiktok_access_token()
    creator = tiktok_query_creator_info(access_token)
    account.creator_username = creator.get('creator_username')
    account.creator_nickname = creator.get('creator_nickname')
    account.creator_avatar_url = creator.get('creator_avatar_url')
    account.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return access_token, account, creator


def _tiktok_error_response(error, default_status=502):
    status = error.status_code if error.status_code and 400 <= error.status_code < 500 else default_status
    return jsonify({'error': str(error), 'tiktok_error': error.to_dict()}), status


def _video_file_for_article(article):
    if not article.video_path:
        raise ValueError('Generate a video before posting to TikTok')

    videos_dir = os.path.realpath(os.path.join(app.root_path, 'static', 'videos'))
    video_path = os.path.realpath(os.path.join(videos_dir, article.video_path))
    if os.path.commonpath([videos_dir, video_path]) != videos_dir:
        raise ValueError('Invalid video path')
    if not os.path.isfile(video_path):
        raise ValueError('Generated video file is missing')
    if not video_path.lower().endswith('.mp4'):
        raise ValueError('TikTok posting currently supports MP4 videos only')
    return video_path


def _video_duration_seconds(video_path):
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(video_path, audio=False)
    try:
        return float(clip.duration)
    finally:
        clip.close()


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


def run_video_in_background(app_context, article_id, image_source="ai", style_override=None, use_video_hook=None):
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
                    image_source=image_source,
                    scenes=scenes,
                    style_key=style_key,
                    emotion=article.dominant_emotion,
                    use_video_hook=use_video_hook,
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
    """Trigger video generation for an article (runs in background).

    Optional JSON body: {"style": "manga"} overrides the auto-picked style.
    """
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400

    if article.status in ('summarizing', 'generating_video', 'generating_carousel'):
        return jsonify({'error': 'Article is already being processed'}), 409

    payload = request.get_json(silent=True) or {}

    image_source = payload.get('image_source', 'ai')
    if image_source not in ('ai', 'stock'):
        image_source = 'ai'

    style_override = payload.get('style')
    if style_override and style_override not in VISUAL_STYLES:
        return jsonify({'error': f'Unknown style: {style_override}'}), 400

    # `use_video_hook` is a tri-state: True/False/None.
    #   True  -> AI video hook (FAL); False -> image hook; None -> env default.
    raw_hook = payload.get('use_video_hook', None)
    if raw_hook is None:
        use_video_hook = None
    else:
        use_video_hook = bool(raw_hook)

    article.status = 'generating_video'
    db.session.commit()

    thread = Thread(
        target=run_video_in_background,
        args=(app.app_context(), article.id, image_source, style_override, use_video_hook)
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

    # Regeneration via ?regenerate=1 or JSON {regenerate: true}
    payload = request.get_json(silent=True) or {}
    force = request.args.get('regenerate') == '1' or payload.get('regenerate') is True

    # Return cached post if already generated (unless force regenerate)
    if article.substack_post and not force:
        return jsonify({'article': article.to_dict()})

    try:
        post = generate_substack_post(article)
        article.substack_post = post
        db.session.commit()
        return jsonify({'article': article.to_dict()})
    except Exception as e:
        logger.error("Substack post generation failed for article %s: %s", article_id, e, exc_info=True)
        return jsonify({'error': 'Failed to generate Substack post'}), 500


@app.route('/api/tiktok/status', methods=['GET'])
def tiktok_connection_status():
    """Return safe connection metadata; OAuth tokens are never serialized."""
    missing = _tiktok_missing_config()
    account = _connected_tiktok_account()
    payload = {
        'configured': not missing,
        'missing_config': missing,
        'connected': account is not None,
        'public_posting_enabled': os.getenv('TIKTOK_ALLOW_PUBLIC_POSTS', 'false').lower() == 'true',
    }
    if account:
        payload.update(account.to_public_dict())
    return jsonify(payload)


@app.route('/api/tiktok/oauth/start', methods=['GET'])
def tiktok_oauth_start():
    """Start TikTok Login Kit authorization with a signed CSRF state."""
    missing = _tiktok_missing_config()
    if missing:
        return jsonify({'error': f"TikTok is not configured: {', '.join(missing)}"}), 503

    config = _tiktok_config()
    state = secrets.token_urlsafe(32)
    session['tiktok_oauth_state'] = state
    session['tiktok_oauth_issued_at'] = int(datetime.now(timezone.utc).timestamp())
    authorize_query = urlencode({
        'client_key': config['client_key'],
        'response_type': 'code',
        'scope': ','.join(TIKTOK_SCOPES),
        'redirect_uri': config['redirect_uri'],
        'state': state,
    })
    authorize_url = f"{TIKTOK_AUTH_URL}?{authorize_query}"
    return redirect(authorize_url)


@app.route('/api/tiktok/oauth/callback', methods=['GET'])
def tiktok_oauth_callback():
    """Validate TikTok's callback, exchange the code, and store encrypted tokens."""
    expected_state = session.pop('tiktok_oauth_state', None)
    issued_at = session.pop('tiktok_oauth_issued_at', None)
    returned_state = request.args.get('state', '')
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if (
        not expected_state
        or not returned_state
        or not secrets.compare_digest(expected_state, returned_state)
        or not issued_at
        or now_ts - int(issued_at) > 600
    ):
        logger.warning('Rejected TikTok OAuth callback with invalid or expired state')
        return redirect('/?tiktok=error&reason=invalid_state')

    if request.args.get('error'):
        logger.warning('TikTok OAuth denied: %s', request.args.get('error'))
        return redirect('/?tiktok=error&reason=authorization_denied')

    code = request.args.get('code', '')
    if not code:
        return redirect('/?tiktok=error&reason=missing_code')

    try:
        config = _tiktok_config()
        token_data = tiktok_exchange_code(
            client_key=config['client_key'],
            client_secret=config['client_secret'],
            code=code,
            redirect_uri=config['redirect_uri'],
        )
        _store_tiktok_tokens(token_data)
        try:
            _refresh_creator_info()
        except TikTokAPIError as creator_error:
            # The OAuth connection is still valid. Creator data will be retried
            # when the user opens the posting dialog.
            logger.warning('Connected TikTok but creator info lookup failed: %s', creator_error)
        return redirect('/?tiktok=connected')
    except (TikTokAPIError, ValueError) as error:
        logger.error('TikTok OAuth callback failed: %s', error)
        return redirect('/?tiktok=error&reason=token_exchange_failed')


@app.route('/api/tiktok/disconnect', methods=['POST'])
def tiktok_disconnect():
    """Revoke the access token when possible, then remove local credentials."""
    account = _connected_tiktok_account()
    if account is None:
        return jsonify({'message': 'TikTok is already disconnected'})

    try:
        config = _tiktok_config()
        access_token = _tiktok_cipher().decrypt(account.access_token_encrypted)
        tiktok_revoke_access_token(
            client_key=config['client_key'],
            client_secret=config['client_secret'],
            access_token=access_token,
        )
    except (TikTokAPIError, ValueError) as error:
        logger.warning('TikTok token revocation failed; removing local token anyway: %s', error)

    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'TikTok disconnected'})


@app.route('/api/tiktok/creator-info', methods=['POST'])
def tiktok_creator_info():
    """Fetch the latest creator settings required by TikTok's posting UX."""
    if _tiktok_missing_config():
        return jsonify({'error': 'TikTok is not configured'}), 503
    try:
        _, account, creator = _refresh_creator_info()
        return jsonify({
            'account': account.to_public_dict(),
            'creator': creator,
            'public_posting_enabled': os.getenv('TIKTOK_ALLOW_PUBLIC_POSTS', 'false').lower() == 'true',
        })
    except TikTokAPIError as error:
        return _tiktok_error_response(error)
    except ValueError as error:
        return jsonify({'error': str(error)}), 500


@app.route('/api/articles/<int:article_id>/tiktok/publish', methods=['POST'])
def tiktok_publish_article(article_id):
    """Upload a generated video through TikTok's Direct Post API."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    payload = request.get_json(silent=True) or {}
    if payload.get('consent') is not True:
        return jsonify({'error': 'TikTok music usage consent is required'}), 400

    try:
        video_path = _video_file_for_article(article)
    except ValueError as error:
        return jsonify({'error': str(error)}), 400

    if article.tiktok_publish_status in TIKTOK_PENDING_STATUSES:
        return jsonify({'error': 'This video already has a TikTok post in progress'}), 409

    title = str(payload.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Enter a TikTok caption'}), 400
    if len(title) > 2_200:
        return jsonify({'error': 'TikTok caption must be 2,200 characters or fewer'}), 400

    privacy_level = str(payload.get('privacy_level') or '').strip()
    if not privacy_level:
        return jsonify({'error': 'Select a TikTok privacy setting'}), 400

    allow_public = os.getenv('TIKTOK_ALLOW_PUBLIC_POSTS', 'false').lower() == 'true'
    if not allow_public and privacy_level != 'SELF_ONLY':
        return jsonify({
            'error': 'This unaudited integration is locked to Only you (SELF_ONLY) posts'
        }), 400

    brand_content = payload.get('brand_content_toggle') is True
    brand_organic = payload.get('brand_organic_toggle') is True
    if brand_content and privacy_level == 'SELF_ONLY':
        return jsonify({
            'error': 'TikTok does not allow branded content posts with Only you privacy'
        }), 400

    article.tiktok_publish_status = 'INITIALIZING'
    article.tiktok_publish_error = None
    db.session.commit()

    try:
        access_token, _, creator = _refresh_creator_info()
        privacy_options = creator.get('privacy_level_options') or []
        if privacy_level not in privacy_options:
            raise ValueError('That privacy setting is not available for this TikTok account')

        duration = _video_duration_seconds(video_path)
        max_duration = int(creator.get('max_video_post_duration_sec') or 0)
        if max_duration and duration > max_duration:
            raise ValueError(
                f'This video is {duration:.1f}s; the connected account allows up to {max_duration}s'
            )

        allow_comment = payload.get('allow_comment') is True and not creator.get('comment_disabled', False)
        allow_duet = payload.get('allow_duet') is True and not creator.get('duet_disabled', False)
        allow_stitch = payload.get('allow_stitch') is True and not creator.get('stitch_disabled', False)

        upload_plan = make_upload_plan(os.path.getsize(video_path))
        initialized = initialize_video_post(
            access_token=access_token,
            title=title,
            privacy_level=privacy_level,
            disable_comment=not allow_comment,
            disable_duet=not allow_duet,
            disable_stitch=not allow_stitch,
            brand_content_toggle=brand_content,
            brand_organic_toggle=brand_organic,
            upload_plan=upload_plan,
        )

        article.tiktok_publish_id = initialized['publish_id']
        article.tiktok_publish_status = 'UPLOADING'
        db.session.commit()

        upload_video_file(initialized['upload_url'], video_path, upload_plan)
        article.tiktok_publish_status = 'PROCESSING_UPLOAD'
        db.session.commit()

        return jsonify({
            'message': 'Video uploaded to TikTok for processing',
            'article': article.to_dict(),
            'publish_id': article.tiktok_publish_id,
        }), 202
    except (TikTokAPIError, ValueError) as error:
        logger.error('TikTok publish failed for article %s: %s', article_id, error)
        article.tiktok_publish_status = 'FAILED'
        article.tiktok_publish_error = str(error)
        db.session.commit()
        if isinstance(error, TikTokAPIError):
            return _tiktok_error_response(error)
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        logger.error('Unexpected TikTok publish failure for article %s: %s', article_id, error, exc_info=True)
        article.tiktok_publish_status = 'FAILED'
        article.tiktok_publish_error = 'Unexpected upload failure'
        db.session.commit()
        return jsonify({'error': 'Unexpected TikTok upload failure'}), 500


@app.route('/api/articles/<int:article_id>/tiktok/status', methods=['POST'])
def tiktok_article_publish_status(article_id):
    """Refresh one article's Direct Post status from TikTok."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404
    if not article.tiktok_publish_id:
        return jsonify({'error': 'This article has not been sent to TikTok'}), 400

    try:
        access_token, _ = _tiktok_access_token()
        status_data = fetch_publish_status(access_token, article.tiktok_publish_id)
        status = status_data.get('status') or article.tiktok_publish_status or 'UNKNOWN'
        article.tiktok_publish_status = status
        article.tiktok_publish_error = status_data.get('fail_reason') or None
        if status == 'PUBLISH_COMPLETE' and not article.tiktok_published_at:
            article.tiktok_published_at = datetime.now(timezone.utc)
        db.session.commit()
        return jsonify({'article': article.to_dict(), 'tiktok': status_data})
    except TikTokAPIError as error:
        article.tiktok_publish_error = str(error)
        db.session.commit()
        return _tiktok_error_response(error)


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

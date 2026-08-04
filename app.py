"""Flask API for Clipper - Article to TikTok Video Generator."""

import os
import json
import re
import logging
import ipaddress
import socket
import secrets
import hashlib
import hmac
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, urlencode, quote
from threading import Event, Lock, Thread
from tempfile import TemporaryDirectory
import shutil
import zipfile
from io import BytesIO
from flask import Flask, request, jsonify, send_from_directory, send_file, session, redirect
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import selectinload
from discovery_web import discovery_bp, ensure_discovery_scheduler

load_dotenv()

from models import (
    db,
    Article,
    find_matching_hook_index,
    PlatformPost,
    PublisherAccount,
    TikTokAccount,
    valid_hook_index,
    VideoMetrics,
)
from summarizer import (
    HASHTAG_MAX_CHARS,
    SEARCH_CAPTION_MAX_CHARS,
    CTA_QUESTION_MAX_CHARS,
    summarize_article,
)
from video_generator import (
    DEFAULT_COLOR_INTENSITY,
    generate_video,
    normalize_color_intensity,
)
import tts_engine
from carousel_generator import generate_carousel
from visual_styles import (
    DEFAULT_STYLE,
    STYLES as VISUAL_STYLES,
    get_style,
    list_styles,
)
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
from performance_metrics import (
    ensure_metrics_scheduler,
    record_public_post_id,
    refresh_video_metrics,
)
from generation_budget import get_generation_budget
from publishers import (
    FacebookPublisher,
    InstagramPublisher,
    PublishResult,
    PublisherError,
    TikTokPublisher,
    YouTubePublisher,
)
from publishers.base import request_with_retries, response_json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

VOICE_TONES = frozenset(tts_engine.VOICE_TONE_PRESETS)
VOICE_PREVIEW_TEXT = (
    "A single bolt of lightning can heat the air five times hotter than the "
    "surface of the sun. The surrounding air expands so fast that we hear thunder."
)


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
        'User-Agent': (
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/126.0.0.0 Safari/537.36'
        ),
        'Accept': (
            'text/html,application/xhtml+xml,application/xml;q=0.9,'
            'image/avif,image/webp,*/*;q=0.8'
        ),
        'Accept-Language': 'en-US,en;q=0.9',
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

# Story discovery lives in a blueprint so its scheduler and transient shortlist
# state stay isolated from the article/video routes in this module.
app.register_blueprint(discovery_bp)

# Database configuration
db_path = os.getenv('DATABASE_URI', f'sqlite:///{os.path.abspath("instance/database.db")}')
app.config['SQLALCHEMY_DATABASE_URI'] = db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


def _migrate_schema():
    """Idempotent SQLite migration for additive columns and metrics table."""
    from sqlalchemy import text, inspect

    new_cols = [
        ("scenes", "TEXT"),
        ("hook_variants", "TEXT"),
        ("best_hook_index", "INTEGER"),
        ("hook_index_used", "INTEGER"),
        ("dominant_emotion", "VARCHAR(32)"),
        ("style", "VARCHAR(32)"),
        ("color_intensity", "VARCHAR(16)"),
        ("visual_sources", "TEXT"),
        ("cover_line", "VARCHAR(128)"),
        ("cta_question", "VARCHAR(512)"),
        ("search_caption", "TEXT"),
        ("series_lane", "VARCHAR(32)"),
        ("substack_post", "TEXT"),
        ("carousel_dir", "VARCHAR(512)"),
        ("carousel_audio", "VARCHAR(512)"),
        ("carousel_generated_at", "DATETIME"),
        ("viral_score", "FLOAT"),
        ("video_generation_token", "VARCHAR(64)"),
        ("tiktok_publish_id", "VARCHAR(256)"),
        ("tiktok_publish_status", "VARCHAR(64)"),
        ("tiktok_publish_error", "TEXT"),
        ("tiktok_published_at", "DATETIME"),
        ("tiktok_approval_message_id", "VARCHAR(64)"),
        ("tiktok_approval_requested_at", "DATETIME"),
        ("pending_publish_request", "TEXT"),
    ]

    with app.app_context():
        # ``create_all`` normally creates this first, while this explicit,
        # check-first call keeps the no-Alembic migration contract visible and
        # safe for existing SQLite installs.
        VideoMetrics.__table__.create(bind=db.engine, checkfirst=True)
        PlatformPost.__table__.create(bind=db.engine, checkfirst=True)
        PublisherAccount.__table__.create(bind=db.engine, checkfirst=True)
        inspector = inspect(db.engine)
        if "articles" not in inspector.get_table_names():
            return
        existing = {c["name"] for c in inspector.get_columns("articles")}
        with db.engine.begin() as conn:
            for col_name, col_type in new_cols:
                if col_name not in existing:
                    conn.execute(text(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}"))
                    logger.info(f"Schema migrated: added articles.{col_name}")

            # Existing installs already contain two real TikTok posts in the
            # legacy Article columns. Copy them into the platform-neutral table
            # once without deleting the compatibility fields.
            conn.execute(text("""
                INSERT INTO platform_posts (
                    article_id, platform, external_id, status, error,
                    published_at, created_at, updated_at
                )
                SELECT
                    a.id, 'tiktok', a.tiktok_publish_id,
                    CASE
                        WHEN a.tiktok_publish_status = 'PUBLISH_COMPLETE'
                            THEN 'PUBLISHED'
                        ELSE COALESCE(a.tiktok_publish_status, 'UNKNOWN')
                    END,
                    a.tiktok_publish_error, a.tiktok_published_at,
                    COALESCE(a.tiktok_published_at, CURRENT_TIMESTAMP),
                    CURRENT_TIMESTAMP
                FROM articles AS a
                WHERE (
                    a.tiktok_publish_id IS NOT NULL
                    OR a.tiktok_publish_status IS NOT NULL
                )
                AND NOT EXISTS (
                    SELECT 1 FROM platform_posts AS p
                    WHERE p.article_id = a.id AND p.platform = 'tiktok'
                )
            """))


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

TIKTOK_POSTING_SCOPES = (
    'user.info.basic',
    'video.publish',
)
TIKTOK_METRICS_SCOPES = (
    'video.list',
    'user.info.stats',
)
TIKTOK_REMOTE_PENDING_STATUSES = {
    'INITIALIZING',
    'UPLOADING',
    'PROCESSING_UPLOAD',
    'PROCESSING_DOWNLOAD',
}

# ``AWAITING_APPROVAL`` is a retired auto-publish state. It remains readable
# only so installations upgraded from the old Discord approval workflow can
# reclaim those rows safely through the cancel endpoint.
LEGACY_RECLAIMABLE_PUBLISH_STATUSES = {'AWAITING_APPROVAL'}
TIKTOK_PENDING_STATUSES = (
    TIKTOK_REMOTE_PENDING_STATUSES | LEGACY_RECLAIMABLE_PUBLISH_STATUSES
)
ACTIVE_PLATFORM_POST_STATUSES = {
    'INITIALIZING',
    'UPLOADING',
    'PROCESSING_UPLOAD',
    'PROCESSING_DOWNLOAD',
    'PROCESSING_CONTAINER',
}
TIKTOK_GENERIC_PUBLISH_ERROR = 'TikTok could not publish this video'
TIKTOK_SAFE_ERROR_MESSAGES = {
    'unaudited_client_can_only_post_to_private_accounts': (
        'TikTok requires this unaudited app to post from a TikTok account that '
        'is set to Private. Turn on Private account in TikTok, then retry with '
        'Only you (SELF_ONLY).'
    ),
    'spam_risk_too_many_posts': (
        "This TikTok account has reached its 24-hour API posting cap. Try again "
        'after the cap resets, or post from the TikTok app.'
    ),
    'spam_risk_too_many_pending_share': (
        'This TikTok account has too many pending API uploads. Complete or wait '
        'for those uploads, then try again later.'
    ),
    'reached_active_user_cap': (
        "This TikTok app has reached its 24-hour active-user publishing cap. "
        'Try again after the cap resets.'
    ),
    'spam_risk_user_banned_from_posting': (
        'TikTok has blocked this account from creating new posts. Check the '
        'account in TikTok; retrying from Clipper will not fix it.'
    ),
    'rate_limit_exceeded': (
        'TikTok is receiving too many requests. Wait a few minutes and try again.'
    ),
    'privacy_level_option_mismatch': (
        'That privacy option is no longer available for this TikTok account. '
        'Reopen the posting window and choose one of the current options.'
    ),
    'scope_not_authorized': (
        'TikTok posting permission is missing. Reconnect the TikTok account and '
        'approve the posting permission.'
    ),
    'access_token_invalid': 'Reconnect your TikTok account and try again.',
    'not_connected': 'Connect your TikTok account and try again.',
    'refresh_expired': 'Reconnect your TikTok account and try again.',
}


def _env_flag(name, default=False):
    """Read a conventional boolean environment variable."""
    fallback = 'true' if default else 'false'
    return os.getenv(name, fallback).strip().lower() in {'1', 'true', 'yes', 'on'}


def _tiktok_requested_scopes():
    """Return only scopes that the configured TikTok app can request.

    TikTok rejects the complete Login Kit request when even one scope is not
    enabled for the developer app. Posting therefore uses its two required
    scopes by default. Operators can opt into the Display API metrics scopes
    after TikTok has enabled those products/scopes for their app.
    """
    scopes = list(TIKTOK_POSTING_SCOPES)
    if _env_flag('TIKTOK_REQUEST_METRICS_SCOPES', False):
        scopes.extend(TIKTOK_METRICS_SCOPES)
    return tuple(scopes)


class TikTokPublishRequestError(ValueError):
    """A caller-correctable publish request error with an HTTP status."""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def _cancel_reclaimable_publish_state(article, *, require_state=True):
    """Clear only retired local approval state; never cancel a remote upload."""
    blockers = []
    if article.tiktok_publish_status in TIKTOK_REMOTE_PENDING_STATUSES:
        blockers.append(('tiktok', article.tiktok_publish_status))
    elif (
        article.tiktok_publish_status in LEGACY_RECLAIMABLE_PUBLISH_STATUSES
        and article.tiktok_publish_id
    ):
        blockers.append(('tiktok', article.tiktok_publish_status))

    for post in article.platform_posts:
        if post.status in ACTIVE_PLATFORM_POST_STATUSES:
            blockers.append((post.platform, post.status))
        elif (
            post.status in LEGACY_RECLAIMABLE_PUBLISH_STATUSES
            and post.external_id
        ):
            blockers.append((post.platform, post.status))

    if blockers:
        blocker_text = ', '.join(
            f'{platform}: {status}' for platform, status in blockers
        )
        raise TikTokPublishRequestError(
            f'Cannot cancel because a remote publish may be in flight '
            f'({blocker_text}). An active remote upload cannot be cancelled '
            'safely; wait for it to finish or refresh its status.',
            409,
        )

    cancelled_platforms = []
    reclaimed = False
    if (
        article.tiktok_publish_status in LEGACY_RECLAIMABLE_PUBLISH_STATUSES
        and not article.tiktok_publish_id
    ):
        article.tiktok_publish_status = None
        article.tiktok_publish_error = None
        cancelled_platforms.append('tiktok')
        reclaimed = True

    for post in article.platform_posts:
        if (
            post.status in LEGACY_RECLAIMABLE_PUBLISH_STATUSES
            and not post.external_id
        ):
            post.status = 'CANCELLED'
            post.error = None
            post.updated_at = datetime.now(timezone.utc)
            if post.platform not in cancelled_platforms:
                cancelled_platforms.append(post.platform)
            reclaimed = True

    if not reclaimed:
        if require_state:
            raise TikTokPublishRequestError(
                'Nothing is awaiting approval for this video. Refresh the '
                'article before trying to cancel again.',
                409,
            )
        return []

    # These nullable columns remain in the additive SQLite schema solely for
    # safe upgrades from the retired Discord approval workflow.
    article.tiktok_approval_message_id = None
    article.tiktok_approval_requested_at = None
    article.pending_publish_request = None
    db.session.commit()
    return cancelled_platforms


def _clear_legacy_awaiting_approvals():
    """Idempotent startup repair for approval rows created by old releases."""
    repaired_articles = 0
    repaired_platforms = 0
    with app.app_context():
        candidate_ids = {
            article_id
            for (article_id,) in db.session.query(Article.id).filter(
                Article.tiktok_publish_status == 'AWAITING_APPROVAL',
                Article.tiktok_publish_id.is_(None),
            ).all()
        }
        candidate_ids.update(
            article_id
            for (article_id,) in db.session.query(PlatformPost.article_id).filter(
                PlatformPost.status == 'AWAITING_APPROVAL',
                PlatformPost.external_id.is_(None),
            ).all()
        )
        for article_id in sorted(candidate_ids):
            article = db.session.get(Article, article_id)
            if not article:
                continue
            try:
                cancelled = _cancel_reclaimable_publish_state(
                    article,
                    require_state=False,
                )
            except TikTokPublishRequestError:
                logger.warning(
                    'Skipped legacy approval cleanup for article %s because '
                    'another publish is active',
                    article_id,
                )
                db.session.rollback()
                continue
            if cancelled:
                repaired_articles += 1
                repaired_platforms += len(cancelled)

    if repaired_articles:
        logger.info(
            'Cleared retired approval state for %s article(s), %s platform(s)',
            repaired_articles,
            repaired_platforms,
        )
    return {
        'articles': repaired_articles,
        'platforms': repaired_platforms,
    }


try:
    _clear_legacy_awaiting_approvals()
except Exception:
    logger.error('Legacy approval-state cleanup failed', exc_info=True)


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


def _tiktok_granted_scopes(account):
    return {
        scope
        for scope in re.split(r'[,\s]+', account.scope or '')
        if scope
    }


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


def _safe_tiktok_error_message(error, fallback):
    """Translate known TikTok codes without exposing raw upstream details."""
    return TIKTOK_SAFE_ERROR_MESSAGES.get(error.code, fallback)


def _log_tiktok_api_error(context, error, *, level=logging.ERROR, exc_info=False):
    """Log every TikTok correlation field needed for support/debugging."""
    logger.log(
        level,
        '%s: code=%s status=%s log_id=%s message=%s',
        context,
        error.code,
        error.status_code,
        error.log_id,
        str(error),
        exc_info=exc_info,
    )


def _tiktok_error_response(error, default_status=502):
    status = error.status_code if error.status_code and 400 <= error.status_code < 500 else default_status
    _log_tiktok_api_error('TikTok API request failed', error, exc_info=True)
    message = _safe_tiktok_error_message(
        error,
        'TikTok could not complete the request. Please try again.',
    )
    return jsonify({
        'error': message,
        'tiktok_error': {'code': error.code, 'log_id': error.log_id},
    }), status


def _video_file_for_article(article):
    if not article.video_path:
        raise ValueError('Generate a video before publishing')

    videos_dir = os.path.realpath(os.path.join(app.root_path, 'static', 'videos'))
    video_path = os.path.realpath(os.path.join(videos_dir, article.video_path))
    if os.path.commonpath([videos_dir, video_path]) != videos_dir:
        raise ValueError('Invalid video path')
    if not os.path.isfile(video_path):
        raise ValueError('Generated video file is missing')
    if not video_path.lower().endswith('.mp4'):
        raise ValueError('Publishing currently supports MP4 videos only')
    return video_path


def _video_duration_seconds(video_path):
    from moviepy.editor import VideoFileClip

    clip = VideoFileClip(video_path, audio=False)
    try:
        return float(clip.duration)
    finally:
        clip.close()


def suggested_tiktok_caption(article):
    """Build search copy + CTA + three hashtags for the posting dialog."""
    hashtags = []
    if article.hashtags:
        try:
            parsed = json.loads(article.hashtags)
            if isinstance(parsed, list):
                hashtags = [
                    str(tag).strip()[:HASHTAG_MAX_CHARS].rstrip()
                    for tag in parsed
                    if str(tag).strip()
                ][:3]
        except (json.JSONDecodeError, TypeError):
            pass
    search_caption = str(
        getattr(article, 'search_caption', None) or article.title
    ).strip()[:SEARCH_CAPTION_MAX_CHARS].rstrip()
    cta_question = str(
        getattr(article, 'cta_question', None) or ''
    ).strip()[:CTA_QUESTION_MAX_CHARS].rstrip()
    # Bound each field before joining instead of slicing the final block. That
    # keeps the required CTA and three hashtags intact at the tail.
    caption_parts = [
        search_caption,
        cta_question,
        ' '.join(hashtags),
    ]
    return '\n\n'.join(part for part in caption_parts if part)


def _make_tiktok_publisher():
    """Build an adapter while retaining patchable legacy client symbols."""
    return TikTokPublisher(
        access_context=_refresh_creator_info,
        duration_seconds=_video_duration_seconds,
        make_upload_plan=make_upload_plan,
        initialize_video_post=initialize_video_post,
        upload_video_file=upload_video_file,
        fetch_publish_status=fetch_publish_status,
        status_access_token=_tiktok_access_token,
        allow_public=_env_flag('TIKTOK_ALLOW_PUBLIC_POSTS', False),
    )


def _mirror_legacy_tiktok_post(article):
    """Keep PlatformPost and the historical Article columns in sync."""
    post = PlatformPost.query.filter_by(
        article_id=article.id,
        platform='tiktok',
    ).first()
    if post is None:
        post = PlatformPost(article_id=article.id, platform='tiktok')
        db.session.add(post)
    post.external_id = article.tiktok_publish_id
    post.status = (
        'PUBLISHED'
        if article.tiktok_publish_status == 'PUBLISH_COMPLETE'
        else (article.tiktok_publish_status or 'UNKNOWN')
    )
    post.error = article.tiktok_publish_error
    post.published_at = article.tiktok_published_at
    post.updated_at = datetime.now(timezone.utc)
    return post


def _publish_conflict_message(status):
    if status in LEGACY_RECLAIMABLE_PUBLISH_STATUSES:
        return (
            f'This video is blocked by the retired {status} state. '
            'Choose Cancel pending post, then retry.'
        )
    return (
        f'This video already has a TikTok post in progress ({status}). '
        'Wait for it to finish or refresh its status; an active remote upload '
        'cannot be cancelled safely.'
    )


def publish_article_to_tiktok(article_id, payload):
    """Validate and upload one article through TikTok Direct Post.

    Publishing is always initiated by an explicit owner request. It must be
    called inside a Flask app context.
    """
    article = db.session.get(Article, article_id)
    if not article:
        raise TikTokPublishRequestError('Article not found', 404)

    payload = payload or {}
    if payload.get('consent') is not True:
        raise TikTokPublishRequestError('TikTok music usage consent is required')

    try:
        video_path = _video_file_for_article(article)
    except ValueError as error:
        raise TikTokPublishRequestError(str(error)) from error

    current_status = article.tiktok_publish_status
    if current_status in TIKTOK_PENDING_STATUSES:
        raise TikTokPublishRequestError(
            _publish_conflict_message(current_status),
            409,
        )

    title = str(payload.get('title') or '').strip()
    if not title:
        raise TikTokPublishRequestError('Enter a TikTok caption')
    if len(title) > 2_200:
        raise TikTokPublishRequestError('TikTok caption must be 2,200 characters or fewer')

    privacy_level = str(payload.get('privacy_level') or '').strip()
    if not privacy_level:
        raise TikTokPublishRequestError('Select a TikTok privacy setting')

    allow_public = _env_flag('TIKTOK_ALLOW_PUBLIC_POSTS', False)
    if not allow_public and privacy_level != 'SELF_ONLY':
        raise TikTokPublishRequestError(
            'This unaudited integration is locked to Only you (SELF_ONLY) posts'
        )

    brand_content = payload.get('brand_content_toggle') is True
    brand_organic = payload.get('brand_organic_toggle') is True
    if brand_content and privacy_level == 'SELF_ONLY':
        raise TikTokPublishRequestError(
            'TikTok does not allow branded content posts with Only you privacy'
        )

    # Compare-and-swap every allowed starting state. This protects two manual
    # requests from both initializing and uploading the same video.
    claim = Article.query.filter(Article.id == article_id)
    if current_status is None:
        claim = claim.filter(Article.tiktok_publish_status.is_(None))
    else:
        claim = claim.filter(Article.tiktok_publish_status == current_status)
    claimed = claim.update(
        {
            Article.tiktok_publish_status: 'INITIALIZING',
            Article.tiktok_publish_error: None,
        },
        synchronize_session=False,
    )
    db.session.commit()
    if claimed != 1:
        db.session.expire_all()
        latest_status = (
            db.session.get(Article, article_id).tiktok_publish_status
            or 'UNKNOWN'
        )
        raise TikTokPublishRequestError(
            _publish_conflict_message(latest_status),
            409,
        )
    article = db.session.get(Article, article_id)

    try:
        result = _make_tiktok_publisher().publish(article, video_path, payload)
        article.tiktok_publish_id = result.external_id
        article.tiktok_publish_status = result.status
        article.tiktok_publish_error = result.error
        _mirror_legacy_tiktok_post(article)
        db.session.commit()
        start_tiktok_status_poller()
        return {
            'message': 'Video uploaded to TikTok for processing',
            'article': article.to_dict(),
            'publish_id': article.tiktok_publish_id,
        }
    except (TikTokAPIError, PublisherError, ValueError) as error:
        if isinstance(error, TikTokAPIError):
            _log_tiktok_api_error(
                f'TikTok publish failed for article {article_id}',
                error,
                exc_info=True,
            )
        else:
            logger.error(
                'TikTok publish failed for article %s: %s',
                article_id,
                error,
                exc_info=True,
            )
        article.tiktok_publish_status = 'FAILED'
        article.tiktok_publish_error = (
            _safe_tiktok_error_message(error, TIKTOK_GENERIC_PUBLISH_ERROR)
            if isinstance(error, TikTokAPIError)
            else (
                error.public_message
                if isinstance(error, PublisherError)
                else str(error)
            )
        )
        _mirror_legacy_tiktok_post(article)
        db.session.commit()
        if isinstance(error, TikTokAPIError):
            raise
        if isinstance(error, PublisherError):
            raise TikTokPublishRequestError(
                error.public_message,
                error.status_code or 400,
            ) from error
        raise TikTokPublishRequestError(str(error)) from error
    except Exception as error:
        logger.error(
            'Unexpected TikTok publish failure for article %s: %s',
            article_id,
            error,
            exc_info=True,
        )
        article.tiktok_publish_status = 'FAILED'
        article.tiktok_publish_error = 'Unexpected upload failure'
        _mirror_legacy_tiktok_post(article)
        db.session.commit()
        raise


def refresh_tiktok_publish_status(article):
    """Fetch and persist TikTok's current state for one submitted article."""
    if not article.tiktok_publish_id:
        raise TikTokPublishRequestError('This article has not been sent to TikTok')

    access_token, _ = _tiktok_access_token()
    status_data = fetch_publish_status(access_token, article.tiktok_publish_id)
    status = status_data.get('status') or article.tiktok_publish_status or 'UNKNOWN'
    fail_reason = status_data.get('fail_reason')
    article.tiktok_publish_status = status
    article.tiktok_publish_error = TIKTOK_GENERIC_PUBLISH_ERROR if fail_reason else None
    if fail_reason:
        logger.error(
            'TikTok reported a publish failure for article %s: %s',
            article.id,
            fail_reason,
        )
    if status == 'PUBLISH_COMPLETE' and not article.tiktok_published_at:
        article.tiktok_published_at = datetime.now(timezone.utc)
    record_public_post_id(article, status_data)
    _mirror_legacy_tiktok_post(article)
    db.session.commit()
    public_status = dict(status_data)
    if fail_reason:
        public_status['fail_reason'] = TIKTOK_GENERIC_PUBLISH_ERROR
    return public_status


def poll_tiktok_publish_statuses_once():
    """Advance all in-flight Direct Posts once; transient failures retry later."""
    articles = Article.query.filter(
        Article.tiktok_publish_id.isnot(None),
        Article.tiktok_publish_status.in_(TIKTOK_REMOTE_PENDING_STATUSES),
    ).all()
    for article in articles:
        try:
            refresh_tiktok_publish_status(article)
        except (TikTokAPIError, TikTokPublishRequestError, ValueError):
            db.session.rollback()
            logger.warning(
                'TikTok status poll failed for article %s; will retry',
                article.id,
                exc_info=True,
            )
        except Exception:
            db.session.rollback()
            logger.error(
                'Unexpected TikTok status poll failure for article %s; will retry',
                article.id,
                exc_info=True,
            )


_tiktok_poller_lock = Lock()
_tiktok_poller_stop = Event()
_tiktok_poller_thread = None


def _tiktok_status_poller_loop():
    try:
        interval = max(5, int(os.getenv('TIKTOK_STATUS_POLL_SECONDS', '15')))
    except ValueError:
        interval = 15
    while not _tiktok_poller_stop.is_set():
        with app.app_context():
            try:
                poll_tiktok_publish_statuses_once()
            except Exception:
                db.session.rollback()
                logger.error('TikTok status poller iteration failed; will retry', exc_info=True)
            finally:
                db.session.remove()
        _tiktok_poller_stop.wait(interval)


def start_tiktok_status_poller():
    """Start one process-local daemon that tracks TikTok processing state."""
    global _tiktok_poller_thread
    if app.config.get('TESTING'):
        return False
    with _tiktok_poller_lock:
        if _tiktok_poller_thread and _tiktok_poller_thread.is_alive():
            return False
        _tiktok_poller_stop.clear()
        _tiktok_poller_thread = Thread(
            target=_tiktok_status_poller_loop,
            name='tiktok-status-poller',
            daemon=True,
        )
        _tiktok_poller_thread.start()
        return True


def refresh_tiktok_metrics_once():
    """Pull current video/account counters for the connected creator."""
    access_token, _ = _tiktok_access_token()
    return refresh_video_metrics(
        access_token,
        caption_builder=suggested_tiktok_caption,
    )


@app.before_request
def _ensure_tiktok_background_services():
    """Lazily start background work in the serving process, never reloader parent."""
    start_tiktok_status_poller()
    for (article_id,) in db.session.query(PlatformPost.article_id).filter_by(
        platform='instagram',
        status='PROCESSING_CONTAINER',
    ).all():
        _start_instagram_container_poller(article_id)
    for (article_id,) in db.session.query(PlatformPost.article_id).filter_by(
        platform='facebook',
        status='PROCESSING_UPLOAD',
    ).all():
        _start_facebook_reel_poller(article_id)
    account = _connected_tiktok_account()
    has_metrics_scopes = account is not None and {
        'video.list',
        'user.info.stats',
    } <= _tiktok_granted_scopes(account)
    if has_metrics_scopes:
        ensure_metrics_scheduler(app, refresh_tiktok_metrics_once)

# ============================================================
# Multi-platform publishing and OAuth helpers
# ============================================================

SUPPORTED_PUBLISH_PLATFORMS = ('tiktok', 'instagram', 'youtube', 'facebook')


def _oauth_encryption_secret():
    return (
        os.getenv('OAUTH_TOKEN_ENCRYPTION_KEY', '').strip()
        or os.getenv('TIKTOK_TOKEN_ENCRYPTION_KEY', '').strip()
        or (_configured_flask_secret or '')
    )


def _oauth_cipher():
    return TokenCipher(_oauth_encryption_secret())


def _publisher_account(platform):
    return PublisherAccount.query.filter_by(platform=platform).first()


def _store_publisher_account(
    platform,
    *,
    access_token,
    refresh_token=None,
    expires_in=None,
    refresh_expires_in=None,
    external_user_id=None,
    username=None,
    scope=None,
):
    if not access_token:
        raise PublisherError(
            f'{platform.title()} did not return an access token',
            code='invalid_token_response',
        )
    now = datetime.now(timezone.utc)
    cipher = _oauth_cipher()
    account = _publisher_account(platform)
    if account is None:
        account = PublisherAccount(platform=platform)
        db.session.add(account)
    account.external_user_id = external_user_id or account.external_user_id
    account.username = username or account.username
    account.access_token_encrypted = cipher.encrypt(access_token)
    if refresh_token:
        account.refresh_token_encrypted = cipher.encrypt(refresh_token)
    account.scope = scope or account.scope
    if expires_in is not None:
        account.access_token_expires_at = now + timedelta(seconds=int(expires_in))
    if refresh_expires_in is not None:
        account.refresh_token_expires_at = now + timedelta(
            seconds=int(refresh_expires_in)
        )
    account.updated_at = now
    db.session.commit()
    return account


def _instagram_config():
    return {
        'app_id': os.getenv('INSTAGRAM_APP_ID', '').strip(),
        'app_secret': os.getenv('INSTAGRAM_APP_SECRET', '').strip(),
        'redirect_uri': os.getenv('INSTAGRAM_REDIRECT_URI', '').strip(),
        'graph_version': os.getenv('INSTAGRAM_GRAPH_VERSION', 'v23.0').strip() or 'v23.0',
    }


def _facebook_config():
    return {
        'app_id': os.getenv('FACEBOOK_APP_ID', '').strip(),
        'app_secret': os.getenv('FACEBOOK_APP_SECRET', '').strip(),
        'redirect_uri': os.getenv('FACEBOOK_REDIRECT_URI', '').strip(),
        'graph_version': os.getenv('FACEBOOK_GRAPH_VERSION', 'v23.0').strip() or 'v23.0',
    }


def _youtube_config():
    return {
        'client_id': os.getenv('YOUTUBE_CLIENT_ID', '').strip(),
        'client_secret': os.getenv('YOUTUBE_CLIENT_SECRET', '').strip(),
        'redirect_uri': os.getenv('YOUTUBE_REDIRECT_URI', '').strip(),
    }


def _public_media_missing_config():
    missing = []
    public_base = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    parsed = urlparse(public_base)
    if parsed.scheme != 'https' or not parsed.netloc:
        missing.append('PUBLIC_BASE_URL (HTTPS)')
    if not (
        os.getenv('PUBLIC_MEDIA_SIGNING_KEY', '').strip()
        or _oauth_encryption_secret()
    ):
        missing.append('PUBLIC_MEDIA_SIGNING_KEY')
    return missing


def _instagram_missing_config():
    config = _instagram_config()
    missing = [
        env_name
        for key, env_name in (
            ('app_id', 'INSTAGRAM_APP_ID'),
            ('app_secret', 'INSTAGRAM_APP_SECRET'),
            ('redirect_uri', 'INSTAGRAM_REDIRECT_URI'),
        )
        if not config[key]
    ]
    if not _oauth_encryption_secret():
        missing.append('OAUTH_TOKEN_ENCRYPTION_KEY')
    return missing + _public_media_missing_config()


def _facebook_missing_config():
    config = _facebook_config()
    missing = [
        env_name
        for key, env_name in (
            ('app_id', 'FACEBOOK_APP_ID'),
            ('app_secret', 'FACEBOOK_APP_SECRET'),
            ('redirect_uri', 'FACEBOOK_REDIRECT_URI'),
        )
        if not config[key]
    ]
    if not _oauth_encryption_secret():
        missing.append('OAUTH_TOKEN_ENCRYPTION_KEY')
    return missing


def _youtube_missing_config():
    config = _youtube_config()
    missing = [
        env_name
        for key, env_name in (
            ('client_id', 'YOUTUBE_CLIENT_ID'),
            ('client_secret', 'YOUTUBE_CLIENT_SECRET'),
            ('redirect_uri', 'YOUTUBE_REDIRECT_URI'),
        )
        if not config[key]
    ]
    if not _oauth_encryption_secret():
        missing.append('OAUTH_TOKEN_ENCRYPTION_KEY')
    return missing


def _signed_public_video_url(filename, *, lifetime_seconds=None):
    """Create a short-lived HTTPS URL Instagram can fetch without Basic Auth."""
    missing = _public_media_missing_config()
    if missing:
        raise PublisherError(
            'Public HTTPS video delivery is not configured',
            code='public_media_not_configured',
            status_code=503,
        )
    lifetime = lifetime_seconds or int(
        os.getenv('PUBLIC_MEDIA_URL_TTL_SECONDS', '21600')
    )
    expires = int(time.time()) + max(900, lifetime)
    signing_key = (
        os.getenv('PUBLIC_MEDIA_SIGNING_KEY', '').strip()
        or _oauth_encryption_secret()
    )
    message = f'{filename}:{expires}'.encode('utf-8')
    signature = hmac.new(
        signing_key.encode('utf-8'),
        message,
        hashlib.sha256,
    ).hexdigest()
    base = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
    return (
        f'{base}/public-media/{quote(filename, safe="/")}'
        f'?expires={expires}&sig={signature}'
    )


def _instagram_access_token():
    account = _publisher_account('instagram')
    if account is None:
        raise PublisherError(
            'Connect Instagram first', code='not_connected', status_code=409
        )
    cipher = _oauth_cipher()
    token = cipher.decrypt(account.access_token_encrypted)
    expiry = _as_utc(account.access_token_expires_at)
    now = datetime.now(timezone.utc)
    if not expiry or expiry > now + timedelta(days=7):
        return token, account

    config = _instagram_config()
    response = request_with_retries(
        requests.get,
        f"https://graph.facebook.com/{config['graph_version']}/oauth/access_token",
        params={
            'grant_type': 'fb_exchange_token',
            'client_id': config['app_id'],
            'client_secret': config['app_secret'],
            'fb_exchange_token': token,
        },
    )
    payload = response_json(response, platform='Instagram')
    account = _store_publisher_account(
        'instagram',
        access_token=payload.get('access_token'),
        expires_in=payload.get('expires_in') or 5_184_000,
        external_user_id=account.external_user_id,
        username=account.username,
        scope=account.scope,
    )
    return _oauth_cipher().decrypt(account.access_token_encrypted), account


def _youtube_access_token():
    account = _publisher_account('youtube')
    if account is None:
        raise PublisherError(
            'Connect YouTube first', code='not_connected', status_code=409
        )
    cipher = _oauth_cipher()
    expiry = _as_utc(account.access_token_expires_at)
    now = datetime.now(timezone.utc)
    if expiry and expiry > now + timedelta(minutes=5):
        return cipher.decrypt(account.access_token_encrypted), account
    if not account.refresh_token_encrypted:
        raise PublisherError(
            'Reconnect YouTube to restore upload access',
            code='refresh_token_missing',
            status_code=409,
        )
    config = _youtube_config()
    response = request_with_retries(
        requests.post,
        'https://oauth2.googleapis.com/token',
        data={
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'refresh_token': cipher.decrypt(account.refresh_token_encrypted),
            'grant_type': 'refresh_token',
        },
    )
    payload = response_json(response, platform='YouTube')
    account = _store_publisher_account(
        'youtube',
        access_token=payload.get('access_token'),
        refresh_token=cipher.decrypt(account.refresh_token_encrypted),
        expires_in=payload.get('expires_in') or 3_600,
        external_user_id=account.external_user_id,
        username=account.username,
        scope=account.scope,
    )
    return _oauth_cipher().decrypt(account.access_token_encrypted), account


def _facebook_access_token():
    account = _publisher_account('facebook')
    if account is None:
        raise PublisherError(
            'Connect Facebook first', code='not_connected', status_code=409
        )
    expiry = _as_utc(account.access_token_expires_at)
    if expiry and expiry <= datetime.now(timezone.utc):
        raise PublisherError(
            'Reconnect Facebook to restore Page publishing access',
            code='access_token_expired',
            status_code=409,
        )
    return _oauth_cipher().decrypt(account.access_token_encrypted), account


def _expiry_metadata(account, *, warning_days):
    if account is None or not account.access_token_expires_at:
        return {'days_until_expiry': None, 'expiry_warning': False, 'expired': False}
    seconds = (
        _as_utc(account.access_token_expires_at) - datetime.now(timezone.utc)
    ).total_seconds()
    return {
        'days_until_expiry': max(0, int(seconds // 86_400)),
        'expiry_warning': seconds <= warning_days * 86_400,
        'expired': seconds <= 0,
    }


def _publisher_status_payload():
    instagram = _publisher_account('instagram')
    facebook = _publisher_account('facebook')
    youtube = _publisher_account('youtube')
    tiktok = _connected_tiktok_account()
    tiktok_missing = _tiktok_missing_config()
    tiktok_payload = {
        'configured': not tiktok_missing,
        'missing_config': tiktok_missing,
        'connected': tiktok is not None,
        'public_posting_enabled': _env_flag('TIKTOK_ALLOW_PUBLIC_POSTS', False),
        'oauth_start_url': '/api/tiktok/oauth/start',
        'disconnect_url': '/api/tiktok/disconnect',
    }
    if tiktok:
        tiktok_payload.update(tiktok.to_public_dict())
        tiktok_payload.update(_expiry_metadata(tiktok, warning_days=7))
        granted = _tiktok_granted_scopes(tiktok)
        missing_posting = [
            scope for scope in TIKTOK_POSTING_SCOPES if scope not in granted
        ]
        tiktok_payload.update({
            'posting_authorized': not missing_posting,
            'missing_posting_scopes': missing_posting,
            'needs_reconsent': bool(missing_posting),
        })

    instagram_missing = _instagram_missing_config()
    instagram_payload = {
        'configured': not instagram_missing,
        'missing_config': instagram_missing,
        'connected': instagram is not None,
        'requirements': (
            'Business or Creator account linked to a Facebook Page; '
            'instagram_content_publish App Review is required for non-test users.'
        ),
        'daily_publish_limit': 50,
        'oauth_start_url': '/api/instagram/oauth/start',
        'disconnect_url': '/api/instagram/disconnect',
    }
    if instagram:
        instagram_payload.update(instagram.to_public_dict())
        instagram_payload.update(_expiry_metadata(instagram, warning_days=10))

    facebook_missing = _facebook_missing_config()
    facebook_payload = {
        'configured': not facebook_missing,
        'missing_config': facebook_missing,
        'connected': facebook is not None,
        'requirements': (
            'Facebook Page access with pages_manage_posts and '
            'pages_read_engagement; App Review is required for non-test users.'
        ),
        'oauth_start_url': '/api/facebook/oauth/start',
        'disconnect_url': '/api/facebook/disconnect',
    }
    if facebook:
        facebook_payload.update(facebook.to_public_dict())
        facebook_payload.update(_expiry_metadata(facebook, warning_days=10))

    youtube_missing = _youtube_missing_config()
    youtube_payload = {
        'configured': not youtube_missing,
        'missing_config': youtube_missing,
        'connected': youtube is not None,
        'video_upload_quota_daily': 100,
        'oauth_start_url': '/api/youtube/oauth/start',
        'disconnect_url': '/api/youtube/disconnect',
        'testing_mode_warning': (
            'OAuth refresh tokens expire after 7 days while the consent screen '
            'is in Testing; unverified projects may upload only private videos.'
        ),
    }
    if youtube:
        youtube_payload.update(youtube.to_public_dict())
        refreshable = bool(youtube.refresh_token_encrypted)
        youtube_payload.update({
            'access_token_refreshable': refreshable,
            **(
                {'days_until_expiry': None, 'expiry_warning': False, 'expired': False}
                if refreshable
                else _expiry_metadata(youtube, warning_days=2)
            ),
        })
    return {
        'platforms': {
            'tiktok': tiktok_payload,
            'instagram': instagram_payload,
            'youtube': youtube_payload,
            'facebook': facebook_payload,
        }
    }


def _make_publisher(platform, article):
    if platform == 'tiktok':
        return _make_tiktok_publisher()
    if platform == 'instagram':
        access_token, account = _instagram_access_token()
        return InstagramPublisher(
            access_token=access_token,
            instagram_user_id=account.external_user_id,
            public_video_url=_signed_public_video_url(article.video_path),
            graph_version=_instagram_config()['graph_version'],
        )
    if platform == 'youtube':
        access_token, _ = _youtube_access_token()
        return YouTubePublisher(access_token=access_token)
    if platform == 'facebook':
        access_token, account = _facebook_access_token()
        return FacebookPublisher(
            access_token=access_token,
            page_id=account.external_user_id,
            graph_version=_facebook_config()['graph_version'],
        )
    raise PublisherError('Unsupported publishing platform', code='unsupported_platform')


def _post_as_result(post, *, idempotent=False):
    accepted = post.status != 'FAILED'
    return PublishResult(
        platform=post.platform,
        status=post.status,
        accepted=accepted,
        external_id=post.external_id,
        permalink=post.permalink,
        error=post.error,
        published_at=(post.published_at.isoformat() if post.published_at else None),
        idempotent=idempotent,
    )


def _claim_platform_post(article_id, platform):
    post = PlatformPost.query.filter_by(
        article_id=article_id,
        platform=platform,
    ).first()
    if post is None:
        post = PlatformPost(
            article_id=article_id,
            platform=platform,
            status='INITIALIZING',
        )
        db.session.add(post)
        try:
            db.session.commit()
            return post, None
        except Exception:
            db.session.rollback()
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform=platform,
            ).first()
            if post is None:
                raise

    if post.status == 'PUBLISHED' or post.status in ACTIVE_PLATFORM_POST_STATUSES:
        return post, _post_as_result(post, idempotent=True)
    post.status = 'INITIALIZING'
    post.error = None
    post.external_id = None
    post.permalink = None
    post.published_at = None
    post.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return post, None


def _persist_publish_result(post, result):
    post.external_id = result.external_id
    post.status = result.status
    post.error = result.error
    post.permalink = result.permalink
    if result.status == 'PUBLISHED' and not post.published_at:
        post.published_at = datetime.now(timezone.utc)
    post.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return _post_as_result(post, idempotent=result.idempotent)


def _publish_one_platform(article_id, platform, options):
    with app.app_context():
        article = db.session.get(Article, article_id)
        if not article:
            return PublishResult(platform, 'FAILED', False, error='Article not found')
        try:
            post, existing = _claim_platform_post(article_id, platform)
            if existing:
                if platform == 'instagram' and existing.status == 'PROCESSING_CONTAINER':
                    _start_instagram_container_poller(article_id)
                if platform == 'facebook' and existing.status == 'PROCESSING_UPLOAD':
                    _start_facebook_reel_poller(article_id)
                return existing
            video_path = _video_file_for_article(article)
            if platform == 'tiktok':
                legacy = publish_article_to_tiktok(
                    article_id,
                    options,
                )
                post = PlatformPost.query.filter_by(
                    article_id=article_id,
                    platform=platform,
                ).one()
                result = PublishResult(
                    platform='tiktok',
                    status=post.status,
                    accepted=True,
                    external_id=legacy.get('publish_id'),
                )
            else:
                result = _make_publisher(platform, article).publish(
                    article,
                    video_path,
                    options,
                )
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform=platform,
            ).one()
            persisted = _persist_publish_result(post, result)
            if platform == 'instagram' and persisted.status == 'PROCESSING_CONTAINER':
                _start_instagram_container_poller(article_id)
            if platform == 'facebook' and persisted.status == 'PROCESSING_UPLOAD':
                _start_facebook_reel_poller(article_id)
            return persisted
        except (PublisherError, TikTokPublishRequestError, TikTokAPIError, ValueError) as error:
            logger.error(
                '%s publish failed for article %s',
                platform,
                article_id,
                exc_info=True,
            )
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform=platform,
            ).first()
            safe_error = (
                error.public_message
                if isinstance(error, PublisherError)
                else (
                    _safe_tiktok_error_message(error, TIKTOK_GENERIC_PUBLISH_ERROR)
                    if isinstance(error, TikTokAPIError)
                    else str(error)
                )
            )
            if post:
                post.status = 'FAILED'
                post.error = safe_error
                post.updated_at = datetime.now(timezone.utc)
                db.session.commit()
                return _post_as_result(post)
            return PublishResult(platform, 'FAILED', False, error=safe_error)
        except Exception:
            logger.error(
                'Unexpected %s publish failure for article %s',
                platform,
                article_id,
                exc_info=True,
            )
            db.session.rollback()
            post = PlatformPost.query.filter_by(
                article_id=article_id,
                platform=platform,
            ).first()
            if post:
                post.status = 'FAILED'
                post.error = f'{platform.title()} could not publish this video'
                post.updated_at = datetime.now(timezone.utc)
                db.session.commit()
                return _post_as_result(post)
            return PublishResult(
                platform,
                'FAILED',
                False,
                error=f'{platform.title()} could not publish this video',
            )
        finally:
            db.session.remove()


def _normalize_publish_options(article, payload, platform):
    shared_caption = str(payload.get('caption') or suggested_tiktok_caption(article))
    raw_options = payload.get('options') or {}
    if not isinstance(raw_options, dict):
        raise TikTokPublishRequestError('Publishing options must be an object')
    platform_options = raw_options.get(platform) or {}
    if not isinstance(platform_options, dict):
        raise TikTokPublishRequestError(
            f'{platform.title()} options must be an object'
        )
    options = dict(platform_options)
    options.setdefault('caption', shared_caption)
    if platform == 'tiktok':
        options.setdefault('title', shared_caption)
        options.setdefault('privacy_level', 'SELF_ONLY')
        options.setdefault('consent', False)
    elif platform == 'youtube':
        options.setdefault('title', article.title)
        options.setdefault('description', shared_caption)
        options.setdefault('privacy_status', 'private')
    return options


def publish_article_everywhere(article_id, payload):
    """Fan out one retry-safe publish request and return per-platform outcomes."""
    if not isinstance(payload, dict):
        raise TikTokPublishRequestError('Publish request must be an object')
    article = db.session.get(Article, article_id)
    if not article:
        raise TikTokPublishRequestError('Article not found', 404)
    try:
        _video_file_for_article(article)
    except ValueError as error:
        raise TikTokPublishRequestError(str(error)) from error
    platforms = payload.get('platforms') if isinstance(payload, dict) else None
    if not isinstance(platforms, list) or not platforms:
        raise TikTokPublishRequestError('Choose at least one publishing platform')
    platforms = list(dict.fromkeys(str(value).lower() for value in platforms))
    invalid = [value for value in platforms if value not in SUPPORTED_PUBLISH_PLATFORMS]
    if invalid:
        raise TikTokPublishRequestError(
            f"Unsupported platform: {', '.join(invalid)}"
        )

    platform_options = {
        platform: _normalize_publish_options(article, payload, platform)
        for platform in platforms
    }
    # Release the coordinator's read transaction before worker app contexts
    # write their independent per-platform rows (important for SQLite).
    db.session.remove()
    outcomes = {}
    with ThreadPoolExecutor(
        max_workers=len(platforms),
        thread_name_prefix=f'publish-{article_id}',
    ) as executor:
        futures = {
            executor.submit(
                _publish_one_platform,
                article_id,
                platform,
                platform_options[platform],
            ): platform
            for platform in platforms
        }
        for future in as_completed(futures):
            platform = futures[future]
            try:
                outcomes[platform] = future.result().to_dict()
            except Exception:
                logger.error(
                    'Publish worker escaped for article %s platform %s',
                    article_id,
                    platform,
                    exc_info=True,
                )
                outcomes[platform] = PublishResult(
                    platform,
                    'FAILED',
                    False,
                    error=f'{platform.title()} could not publish this video',
                ).to_dict()
    article = db.session.get(Article, article_id)
    db.session.refresh(article)
    return {
        'article': article.to_dict(),
        'results': {platform: outcomes[platform] for platform in platforms},
        'all_accepted': all(result['accepted'] for result in outcomes.values()),
    }

_instagram_poll_lock = Lock()
_instagram_polling_articles = set()


def _start_instagram_container_poller(article_id):
    if app.config.get('TESTING'):
        return False
    with _instagram_poll_lock:
        if article_id in _instagram_polling_articles:
            return False
        _instagram_polling_articles.add(article_id)

    def poll():
        deadline = time.monotonic() + max(
            60,
            int(os.getenv('INSTAGRAM_CONTAINER_TIMEOUT_SECONDS', '1200')),
        )
        delay = 5
        try:
            while time.monotonic() < deadline:
                with app.app_context():
                    post = PlatformPost.query.filter_by(
                        article_id=article_id,
                        platform='instagram',
                    ).first()
                    if not post or post.status != 'PROCESSING_CONTAINER':
                        return
                    try:
                        access_token, account = _instagram_access_token()
                        publisher = InstagramPublisher(
                            access_token=access_token,
                            instagram_user_id=account.external_user_id,
                            public_video_url='',
                            graph_version=_instagram_config()['graph_version'],
                        )
                        result = publisher.check_status(post.external_id)
                        _persist_publish_result(post, result)
                        if result.status in {'PUBLISHED', 'FAILED'}:
                            return
                    except Exception:
                        db.session.rollback()
                        logger.warning(
                            'Instagram container poll failed for article %s; retrying',
                            article_id,
                            exc_info=True,
                        )
                    finally:
                        db.session.remove()
                time.sleep(delay)
                delay = min(30, delay * 2)
            with app.app_context():
                post = PlatformPost.query.filter_by(
                    article_id=article_id,
                    platform='instagram',
                ).first()
                if post and post.status == 'PROCESSING_CONTAINER':
                    post.status = 'FAILED'
                    post.error = 'Instagram timed out while processing this video'
                    post.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
        finally:
            with _instagram_poll_lock:
                _instagram_polling_articles.discard(article_id)

    Thread(
        target=poll,
        name=f'instagram-container-{article_id}',
        daemon=True,
    ).start()
    return True


_facebook_poll_lock = Lock()
_facebook_polling_articles = set()


def _start_facebook_reel_poller(article_id):
    if app.config.get('TESTING'):
        return False
    with _facebook_poll_lock:
        if article_id in _facebook_polling_articles:
            return False
        _facebook_polling_articles.add(article_id)

    def poll():
        deadline = time.monotonic() + max(
            60,
            int(os.getenv('FACEBOOK_REEL_TIMEOUT_SECONDS', '1200')),
        )
        delay = 5
        try:
            while time.monotonic() < deadline:
                with app.app_context():
                    post = PlatformPost.query.filter_by(
                        article_id=article_id,
                        platform='facebook',
                    ).first()
                    if not post or post.status != 'PROCESSING_UPLOAD':
                        return
                    try:
                        access_token, account = _facebook_access_token()
                        publisher = FacebookPublisher(
                            access_token=access_token,
                            page_id=account.external_user_id,
                            graph_version=_facebook_config()['graph_version'],
                        )
                        result = publisher.check_status(post.external_id)
                        _persist_publish_result(post, result)
                        if result.status in {'PUBLISHED', 'FAILED'}:
                            return
                    except Exception:
                        db.session.rollback()
                        logger.warning(
                            'Facebook Reel status poll failed for article %s; retrying',
                            article_id,
                            exc_info=True,
                        )
                    finally:
                        db.session.remove()
                time.sleep(delay)
                delay = min(30, delay * 2)
            with app.app_context():
                post = PlatformPost.query.filter_by(
                    article_id=article_id,
                    platform='facebook',
                ).first()
                if post and post.status == 'PROCESSING_UPLOAD':
                    post.status = 'FAILED'
                    post.error = 'Facebook timed out while processing this Reel'
                    post.updated_at = datetime.now(timezone.utc)
                    db.session.commit()
        finally:
            with _facebook_poll_lock:
                _facebook_polling_articles.discard(article_id)

    Thread(
        target=poll,
        name=f'facebook-reel-{article_id}',
        daemon=True,
    ).start()
    return True


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
            article.cover_line = result.get('cover_line') or None
            article.cta_question = result.get('cta_question') or None
            article.search_caption = result.get('search_caption') or None
            article.series_lane = result.get('series_lane') or None

            # Engagement metadata
            scenes = result.get('scenes') or []
            article.scenes = json.dumps(scenes) if scenes else None
            article.visual_sources = None
            hook_variants = result.get('hook_variants') or []
            article.hook_variants = json.dumps(hook_variants) if hook_variants else None
            article.best_hook_index = valid_hook_index(
                result.get('best_hook_index'),
                hook_variants,
            )
            # Re-summarizing replaces the hook options, so an index attributed
            # to the previous list can no longer identify the old MP4's opening
            # accurately. The next successful render restores attribution.
            article.hook_index_used = None
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


def run_video_in_background(
    app_context,
    article_id,
    image_source="ai",
    style_override=None,
    use_video_hook=None,
    voice_tone="controlled",
    generation_token=None,
    color_intensity=DEFAULT_COLOR_INTENSITY,
):
    """Run video generation in a background thread, with a watchdog timeout.

    If generation exceeds VIDEO_TIMEOUT_SECONDS, a separate timer thread flips
    the article to 'failed' so the UI shows a clean terminal state. The worker
    thread itself may keep running (Python can't safely kill threads), so on
    successful completion we re-check status and discard the output if the
    watchdog already declared failure.
    """
    from threading import Timer
    color_intensity = normalize_color_intensity(color_intensity)

    def _watchdog_fire():
        # Runs in a separate thread — needs its own app context.
        with app.app_context():
            article = db.session.get(Article, article_id)
            if (
                article
                and article.status == 'generating_video'
                and article.video_generation_token == generation_token
            ):
                logger.error(
                    f"Video generation for article {article_id} timed out after "
                    f"{VIDEO_TIMEOUT_SECONDS}s. Marking failed. Worker thread may "
                    "still be running and will discard its output on completion."
                )
                article.status = 'failed'
                article.video_generation_token = None
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
                # First renders use the channel's Illustrated Science identity.
                # A style is changed only when the user explicitly chooses one
                # in the existing manual picker.
                style_key = style_override or DEFAULT_STYLE

                visual_sources = []
                video_path = generate_video(
                    article_id=article.id,
                    title=article.title,
                    script=article.video_script,
                    image_source=image_source,
                    scenes=scenes,
                    style_key=style_key,
                    emotion=article.dominant_emotion,
                    use_video_hook=use_video_hook,
                    voice_tone=voice_tone,
                    cover_line=article.cover_line,
                    series_lane=article.series_lane,
                    hero_image=article.hero_image,
                    color_intensity=color_intensity,
                    visual_sources_out=visual_sources,
                )

                # Re-fetch: watchdog may have already marked us failed while we
                # were inside generate_video(). If so, drop the result so we
                # don't revive a failed row.
                db.session.refresh(article)
                if (
                    article.status != 'generating_video'
                    or article.video_generation_token != generation_token
                ):
                    logger.warning(
                        f"Stale video worker for article {article_id} completed "
                        f"after ownership changed (status={article.status}); "
                        f"discarding {video_path}"
                    )
                    try:
                        os.remove(video_path)
                    except OSError:
                        pass
                    return

                article.style = style_key
                article.color_intensity = color_intensity
                article.visual_sources = json.dumps(visual_sources)

                relative_path = os.path.basename(video_path)
                article.video_path = relative_path
                article.hook_index_used = find_matching_hook_index(
                    json.loads(article.hook_variants)
                    if article.hook_variants
                    else [],
                    json.loads(article.scenes) if article.scenes else [],
                )
                article.status = 'video_done'
                article.video_generation_token = None
                article.video_generated_at = datetime.now(timezone.utc)
                db.session.commit()
                logger.info(f"Video generated for article {article_id}")

            except Exception as e:
                logger.error(f"Failed to generate video for article {article_id}: {e}", exc_info=True)
                # Only overwrite status if watchdog hasn't already set it.
                db.session.refresh(article)
                if (
                    article.status == 'generating_video'
                    and article.video_generation_token == generation_token
                ):
                    article.status = 'failed'
                    article.video_generation_token = None
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


@app.route('/public-media/<path:filename>')
def serve_signed_public_video(filename):
    """Serve one generated video through an expiring HMAC URL for Instagram."""
    safe_filename = secure_filename(filename)
    if not safe_filename or safe_filename != filename:
        return jsonify({'error': 'Invalid media link'}), 400
    try:
        expires = int(request.args.get('expires', '0'))
    except ValueError:
        expires = 0
    signature = request.args.get('sig', '')
    if expires <= int(time.time()) or not signature:
        return jsonify({'error': 'Media link expired'}), 403
    signing_key = (
        os.getenv('PUBLIC_MEDIA_SIGNING_KEY', '').strip()
        or _oauth_encryption_secret()
    )
    if not signing_key:
        logger.error('Rejected public media request because no signing key is configured')
        return jsonify({'error': 'Public media delivery is unavailable'}), 503
    expected = hmac.new(
        signing_key.encode('utf-8'),
        f'{filename}:{expires}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return jsonify({'error': 'Invalid media link'}), 403
    response = send_from_directory('static/videos', safe_filename)
    response.headers['Cache-Control'] = 'private, max-age=300'
    return response


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
    articles = (
        Article.query.options(
            selectinload(Article.video_metrics),
            selectinload(Article.platform_posts),
        )
        .order_by(Article.scraped_at.desc())
        .all()
    )
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


@app.route('/api/articles/<int:article_id>/hook', methods=['POST'])
def select_article_hook(article_id):
    """Select one generated hook and keep scenes/script in exact alignment."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    processing_statuses = {
        'summarizing',
        'generating_video',
        'generating_carousel',
    }
    if article.status in processing_statuses:
        return jsonify({'error': 'Article is already being processed'}), 409

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or 'hook_index' not in payload:
        return jsonify({'error': 'hook_index is required'}), 400
    hook_index = payload['hook_index']
    if type(hook_index) is not int:
        return jsonify({'error': 'hook_index must be an integer'}), 400

    try:
        hook_variants = json.loads(article.hook_variants) if article.hook_variants else []
    except (TypeError, ValueError):
        return jsonify({'error': 'Stored hook options are invalid; re-summarize this article'}), 409
    if not isinstance(hook_variants, list) or not hook_variants:
        return jsonify({'error': 'Article has no hook options; re-summarize it first'}), 400
    if hook_index < 0 or hook_index >= len(hook_variants):
        return jsonify({'error': 'hook_index is out of range'}), 400

    selected_hook = hook_variants[hook_index]
    if not isinstance(selected_hook, str) or not selected_hook.strip():
        return jsonify({'error': 'Selected hook is empty; re-summarize this article'}), 409
    selected_hook = selected_hook.strip()

    original_scenes_json = article.scenes
    try:
        scenes = json.loads(original_scenes_json) if original_scenes_json else []
    except (TypeError, ValueError):
        return jsonify({'error': 'Stored scenes are invalid; re-summarize this article'}), 409
    if not isinstance(scenes, list) or not scenes:
        return jsonify({'error': 'Article has no scenes; re-summarize it first'}), 409
    if any(
        not isinstance(scene, dict)
        or not isinstance(scene.get('speech'), str)
        or not scene['speech'].strip()
        for scene in scenes
    ):
        return jsonify({'error': 'Stored scenes are incomplete; re-summarize this article'}), 409

    previous_script = (article.video_script or '').strip()
    previous_opening = scenes[0]['speech'].strip()
    scenes[0] = {**scenes[0], 'speech': selected_hook}
    rewritten_script = ' '.join(scene['speech'].strip() for scene in scenes)
    requires_regeneration = bool(
        article.video_path
        and (
            previous_opening != selected_hook
            or previous_script != rewritten_script
        )
    )

    # Match the generation endpoint's optimistic ownership pattern so a hook
    # change cannot race a render that claims the same Article.
    current_status = article.status
    update = Article.query.filter(
        Article.id == article_id,
        Article.scenes == original_scenes_json,
    )
    if current_status is None:
        update = update.filter(Article.status.is_(None))
    else:
        update = update.filter(Article.status == current_status)
    updated = update.update(
        {
            Article.scenes: json.dumps(scenes),
            Article.video_script: rewritten_script,
        },
        synchronize_session=False,
    )
    if updated != 1:
        db.session.rollback()
        return jsonify({'error': 'Article is already being processed'}), 409
    db.session.commit()

    article = db.session.get(Article, article_id)
    message = f'Hook {hook_index + 1} selected'
    if requires_regeneration:
        message += '. Regenerate the video to use it.'
    return jsonify({
        'message': message,
        'requires_regeneration': requires_regeneration,
        'article': article.to_dict(),
    })


@app.route('/api/tts/preview', methods=['POST'])
def preview_voice_tone():
    """Return a short WAV preview for one of Clipper's voice-tone presets."""
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'JSON body must be an object'}), 400
    voice_tone = payload.get('voice_tone', 'controlled')
    if not isinstance(voice_tone, str) or voice_tone not in VOICE_TONES:
        return jsonify({'error': 'Unknown voice tone'}), 400

    try:
        with TemporaryDirectory(prefix='clipper-voice-preview-') as temp_dir:
            requested_path = os.path.join(temp_dir, f'{voice_tone}.wav')
            audio_path = tts_engine.synthesize(
                VOICE_PREVIEW_TEXT,
                requested_path,
                voice_tone=voice_tone,
            )
            with open(audio_path, 'rb') as audio_file:
                audio_bytes = audio_file.read()

        return send_file(
            BytesIO(audio_bytes),
            mimetype='audio/wav',
            as_attachment=False,
            download_name=f'{voice_tone}-voice-preview.wav',
        )
    except Exception:
        logger.error(
            "Failed to generate voice preview for tone=%s",
            voice_tone,
            exc_info=True,
        )
        return jsonify({
            'error': 'Voice preview is unavailable right now. Please try again.'
        }), 503


@app.route('/api/articles/<int:article_id>/video', methods=['POST'])
def generate_video_endpoint(article_id):
    """Trigger video generation for an article (runs in background).

    Optional JSON body may override the visual style and voice tone.
    """
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404

    if not article.video_script:
        return jsonify({'error': 'Article must be summarized first'}), 400

    if article.status in ('summarizing', 'generating_video', 'generating_carousel'):
        return jsonify({'error': 'Article is already being processed'}), 409

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({'error': 'JSON body must be an object'}), 400

    image_source = payload.get('image_source', 'ai')
    if image_source not in ('ai', 'stock', 'mixed'):
        image_source = 'ai'

    style_override = payload.get('style')
    if style_override and style_override not in VISUAL_STYLES:
        return jsonify({'error': f'Unknown style: {style_override}'}), 400

    voice_tone = payload.get('voice_tone', 'controlled')
    if not isinstance(voice_tone, str) or voice_tone not in VOICE_TONES:
        return jsonify({'error': 'Unknown voice tone'}), 400

    raw_color_intensity = payload.get(
        'color_intensity',
        DEFAULT_COLOR_INTENSITY,
    )
    if (
        not isinstance(raw_color_intensity, str)
        or raw_color_intensity.strip().lower()
        not in {'natural', 'vivid', 'electric'}
    ):
        return jsonify({'error': 'Unknown color intensity'}), 400
    color_intensity = normalize_color_intensity(raw_color_intensity)

    # `use_video_hook` is a tri-state: True/False/None.
    #   True  -> AI video hook (FAL); False -> image hook; None -> env default.
    raw_hook = payload.get('use_video_hook', None)
    if raw_hook is None:
        use_video_hook = None
    else:
        use_video_hook = bool(raw_hook)

    generation_token = secrets.token_hex(24)
    current_status = article.status
    claim = Article.query.filter(Article.id == article_id)
    if current_status is None:
        claim = claim.filter(Article.status.is_(None))
    else:
        claim = claim.filter(Article.status == current_status)
    claimed = claim.update(
        {
            Article.status: 'generating_video',
            Article.video_generation_token: generation_token,
        },
        synchronize_session=False,
    )
    db.session.commit()
    if claimed != 1:
        return jsonify({'error': 'Article is already being processed'}), 409

    article = db.session.get(Article, article_id)

    thread = Thread(
        target=run_video_in_background,
        args=(
            app.app_context(),
            article.id,
            image_source,
            style_override,
            use_video_hook,
            voice_tone,
            generation_token,
        ),
        kwargs={'color_intensity': color_intensity},
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


@app.route('/api/publishers/status', methods=['GET'])
def publishers_connection_status():
    """Return safe connection/readiness metadata for every destination."""
    return jsonify(_publisher_status_payload())


@app.route('/api/instagram/status', methods=['GET'])
def instagram_connection_status():
    return jsonify(_publisher_status_payload()['platforms']['instagram'])


@app.route('/api/youtube/status', methods=['GET'])
def youtube_connection_status():
    return jsonify(_publisher_status_payload()['platforms']['youtube'])


@app.route('/api/facebook/status', methods=['GET'])
def facebook_connection_status():
    return jsonify(_publisher_status_payload()['platforms']['facebook'])


@app.route('/api/instagram/oauth/start', methods=['GET'])
def instagram_oauth_start():
    missing = _instagram_missing_config()
    if missing:
        return jsonify({'error': 'Instagram publishing is not configured', 'missing_config': missing}), 503
    config = _instagram_config()
    state = secrets.token_urlsafe(32)
    session['instagram_oauth_state'] = state
    session['instagram_oauth_issued_at'] = int(datetime.now(timezone.utc).timestamp())
    return redirect(
        'https://www.facebook.com/'
        f"{config['graph_version']}/dialog/oauth?"
        + urlencode({
            'client_id': config['app_id'],
            'redirect_uri': config['redirect_uri'],
            'state': state,
            'response_type': 'code',
            'scope': ','.join((
                'instagram_basic',
                'instagram_content_publish',
                'pages_show_list',
                'pages_read_engagement',
            )),
        })
    )


@app.route('/api/instagram/oauth/callback', methods=['GET'])
def instagram_oauth_callback():
    expected = session.pop('instagram_oauth_state', None)
    issued_at = session.pop('instagram_oauth_issued_at', None)
    returned = request.args.get('state', '')
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if (
        not expected
        or not returned
        or not secrets.compare_digest(expected, returned)
        or not issued_at
        or now_ts - int(issued_at) > 600
    ):
        logger.warning('Rejected Instagram OAuth callback with invalid or expired state')
        return redirect('/?instagram=error&reason=invalid_state')
    if request.args.get('error') or not request.args.get('code'):
        return redirect('/?instagram=error&reason=authorization_denied')
    try:
        config = _instagram_config()
        token_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/oauth/access_token",
            params={
                'client_id': config['app_id'],
                'client_secret': config['app_secret'],
                'redirect_uri': config['redirect_uri'],
                'code': request.args['code'],
            },
        )
        token_payload = response_json(token_response, platform='Instagram')
        short_token = token_payload.get('access_token')
        long_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/oauth/access_token",
            params={
                'grant_type': 'fb_exchange_token',
                'client_id': config['app_id'],
                'client_secret': config['app_secret'],
                'fb_exchange_token': short_token,
            },
        )
        long_payload = response_json(long_response, platform='Instagram')
        access_token = long_payload.get('access_token') or short_token
        pages_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/me/accounts",
            params={
                'fields': 'id,name,instagram_business_account{id,username}',
                'access_token': access_token,
            },
        )
        pages = response_json(pages_response, platform='Instagram').get('data') or []
        instagram_profile = next(
            (
                page.get('instagram_business_account')
                for page in pages
                if page.get('instagram_business_account')
            ),
            None,
        )
        if not instagram_profile or not instagram_profile.get('id'):
            raise PublisherError(
                'No Instagram Business or Creator account is linked to this Facebook Page',
                code='instagram_account_missing',
            )
        _store_publisher_account(
            'instagram',
            access_token=access_token,
            expires_in=long_payload.get('expires_in') or 5_184_000,
            external_user_id=str(instagram_profile['id']),
            username=instagram_profile.get('username'),
            scope='instagram_basic instagram_content_publish',
        )
        return redirect('/?instagram=connected')
    except Exception:
        logger.error('Instagram OAuth callback failed', exc_info=True)
        return redirect('/?instagram=error&reason=token_exchange_failed')


@app.route('/api/instagram/disconnect', methods=['POST'])
def instagram_disconnect():
    account = _publisher_account('instagram')
    if account is None:
        return jsonify({'message': 'Instagram is already disconnected'})
    try:
        token = _oauth_cipher().decrypt(account.access_token_encrypted)
        request_with_retries(
            requests.delete,
            f"https://graph.facebook.com/{_instagram_config()['graph_version']}/me/permissions",
            params={'access_token': token},
            retries=1,
        )
    except Exception:
        logger.warning('Instagram token revocation failed; removing local token', exc_info=True)
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Instagram disconnected'})


@app.route('/api/facebook/oauth/start', methods=['GET'])
def facebook_oauth_start():
    missing = _facebook_missing_config()
    if missing:
        return jsonify({
            'error': 'Facebook publishing is not configured',
            'missing_config': missing,
        }), 503
    config = _facebook_config()
    state = secrets.token_urlsafe(32)
    session['facebook_oauth_state'] = state
    session['facebook_oauth_issued_at'] = int(
        datetime.now(timezone.utc).timestamp()
    )
    return redirect(
        'https://www.facebook.com/'
        f"{config['graph_version']}/dialog/oauth?"
        + urlencode({
            'client_id': config['app_id'],
            'redirect_uri': config['redirect_uri'],
            'state': state,
            'response_type': 'code',
            'scope': ','.join((
                'pages_show_list',
                'pages_manage_posts',
                'pages_read_engagement',
            )),
        })
    )


@app.route('/api/facebook/oauth/callback', methods=['GET'])
def facebook_oauth_callback():
    expected = session.pop('facebook_oauth_state', None)
    issued_at = session.pop('facebook_oauth_issued_at', None)
    returned = request.args.get('state', '')
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if (
        not expected
        or not returned
        or not secrets.compare_digest(expected, returned)
        or not issued_at
        or now_ts - int(issued_at) > 600
    ):
        logger.warning('Rejected Facebook OAuth callback with invalid or expired state')
        return redirect('/?facebook=error&reason=invalid_state')
    if request.args.get('error') or not request.args.get('code'):
        return redirect('/?facebook=error&reason=authorization_denied')
    try:
        config = _facebook_config()
        token_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/oauth/access_token",
            params={
                'client_id': config['app_id'],
                'client_secret': config['app_secret'],
                'redirect_uri': config['redirect_uri'],
                'code': request.args['code'],
            },
        )
        token_payload = response_json(token_response, platform='Facebook')
        short_token = token_payload.get('access_token')
        long_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/oauth/access_token",
            params={
                'grant_type': 'fb_exchange_token',
                'client_id': config['app_id'],
                'client_secret': config['app_secret'],
                'fb_exchange_token': short_token,
            },
        )
        long_payload = response_json(long_response, platform='Facebook')
        user_token = long_payload.get('access_token') or short_token
        pages_response = request_with_retries(
            requests.get,
            f"https://graph.facebook.com/{config['graph_version']}/me/accounts",
            params={
                'fields': 'id,name,access_token',
                'access_token': user_token,
            },
        )
        pages = response_json(pages_response, platform='Facebook').get('data') or []
        page = next(
            (
                value
                for value in pages
                if value.get('id') and value.get('access_token')
            ),
            None,
        )
        if not page:
            raise PublisherError(
                'No manageable Facebook Page is available for Reel publishing',
                code='facebook_page_missing',
            )
        _store_publisher_account(
            'facebook',
            access_token=page['access_token'],
            expires_in=long_payload.get('expires_in'),
            external_user_id=str(page['id']),
            username=page.get('name'),
            scope='pages_show_list pages_manage_posts pages_read_engagement',
        )
        return redirect('/?facebook=connected')
    except Exception:
        logger.error('Facebook OAuth callback failed', exc_info=True)
        return redirect('/?facebook=error&reason=token_exchange_failed')


@app.route('/api/facebook/disconnect', methods=['POST'])
def facebook_disconnect():
    account = _publisher_account('facebook')
    if account is None:
        return jsonify({'message': 'Facebook is already disconnected'})
    try:
        token = _oauth_cipher().decrypt(account.access_token_encrypted)
        request_with_retries(
            requests.delete,
            f"https://graph.facebook.com/{_facebook_config()['graph_version']}/me/permissions",
            params={'access_token': token},
            retries=1,
        )
    except Exception:
        logger.warning(
            'Facebook token revocation failed; removing local token',
            exc_info=True,
        )
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'Facebook disconnected'})


@app.route('/api/youtube/oauth/start', methods=['GET'])
def youtube_oauth_start():
    missing = _youtube_missing_config()
    if missing:
        return jsonify({'error': 'YouTube publishing is not configured', 'missing_config': missing}), 503
    config = _youtube_config()
    state = secrets.token_urlsafe(32)
    session['youtube_oauth_state'] = state
    session['youtube_oauth_issued_at'] = int(datetime.now(timezone.utc).timestamp())
    return redirect(
        'https://accounts.google.com/o/oauth2/v2/auth?'
        + urlencode({
            'client_id': config['client_id'],
            'redirect_uri': config['redirect_uri'],
            'response_type': 'code',
            'scope': 'https://www.googleapis.com/auth/youtube.upload',
            'access_type': 'offline',
            'include_granted_scopes': 'true',
            'prompt': 'consent',
            'state': state,
        })
    )


@app.route('/api/youtube/oauth/callback', methods=['GET'])
def youtube_oauth_callback():
    expected = session.pop('youtube_oauth_state', None)
    issued_at = session.pop('youtube_oauth_issued_at', None)
    returned = request.args.get('state', '')
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if (
        not expected
        or not returned
        or not secrets.compare_digest(expected, returned)
        or not issued_at
        or now_ts - int(issued_at) > 600
    ):
        logger.warning('Rejected YouTube OAuth callback with invalid or expired state')
        return redirect('/?youtube=error&reason=invalid_state')
    if request.args.get('error') or not request.args.get('code'):
        return redirect('/?youtube=error&reason=authorization_denied')
    try:
        config = _youtube_config()
        token_response = request_with_retries(
            requests.post,
            'https://oauth2.googleapis.com/token',
            data={
                'client_id': config['client_id'],
                'client_secret': config['client_secret'],
                'code': request.args['code'],
                'redirect_uri': config['redirect_uri'],
                'grant_type': 'authorization_code',
            },
        )
        token_payload = response_json(token_response, platform='YouTube')
        access_token = token_payload.get('access_token')
        channel_response = request_with_retries(
            requests.get,
            'https://www.googleapis.com/youtube/v3/channels',
            params={'part': 'snippet', 'mine': 'true'},
            headers={'Authorization': f'Bearer {access_token}'},
        )
        items = response_json(channel_response, platform='YouTube').get('items') or []
        channel = items[0] if items else {}
        _store_publisher_account(
            'youtube',
            access_token=access_token,
            refresh_token=token_payload.get('refresh_token'),
            expires_in=token_payload.get('expires_in') or 3_600,
            external_user_id=channel.get('id'),
            username=(channel.get('snippet') or {}).get('title'),
            scope=token_payload.get('scope') or 'https://www.googleapis.com/auth/youtube.upload',
        )
        return redirect('/?youtube=connected')
    except Exception:
        logger.error('YouTube OAuth callback failed', exc_info=True)
        return redirect('/?youtube=error&reason=token_exchange_failed')


@app.route('/api/youtube/disconnect', methods=['POST'])
def youtube_disconnect():
    account = _publisher_account('youtube')
    if account is None:
        return jsonify({'message': 'YouTube is already disconnected'})
    try:
        token = _oauth_cipher().decrypt(account.access_token_encrypted)
        request_with_retries(
            requests.post,
            'https://oauth2.googleapis.com/revoke',
            params={'token': token},
            retries=1,
        )
    except Exception:
        logger.warning('YouTube token revocation failed; removing local token', exc_info=True)
    db.session.delete(account)
    db.session.commit()
    return jsonify({'message': 'YouTube disconnected'})


@app.route('/api/articles/<int:article_id>/publish', methods=['POST'])
def publish_article_multi_platform(article_id):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'Publish request must be an object'}), 400
    try:
        result = publish_article_everywhere(article_id, payload)
    except TikTokPublishRequestError as error:
        return jsonify({'error': str(error)}), error.status_code
    # Accepted uploads may still be processing remotely. Partial or complete
    # failures use HTTP 207 while retaining every platform's durable outcome.
    return jsonify(result), 202 if result['all_accepted'] else 207


@app.route('/api/articles/<int:article_id>/publish/cancel', methods=['POST'])
def cancel_article_publish(article_id):
    """Reclaim a retired local approval state without touching remote posts."""
    article = db.session.get(Article, article_id)
    if not article:
        return jsonify({'error': 'Article not found'}), 404
    try:
        cancelled_platforms = _cancel_reclaimable_publish_state(article)
    except TikTokPublishRequestError as error:
        return jsonify({'error': str(error)}), error.status_code
    db.session.refresh(article)
    return jsonify({
        'message': (
            'Pending approval cancelled. You can choose Post and publish '
            'manually now.'
        ),
        'cancelled_platforms': cancelled_platforms,
        'article': article.to_dict(),
    })


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
        granted_scopes = _tiktok_granted_scopes(account)
        missing_posting_scopes = [
            scope for scope in TIKTOK_POSTING_SCOPES if scope not in granted_scopes
        ]
        missing_metrics_scopes = [
            scope for scope in TIKTOK_METRICS_SCOPES if scope not in granted_scopes
        ]
        request_metrics_scopes = _env_flag('TIKTOK_REQUEST_METRICS_SCOPES', False)
        missing_requested_scopes = missing_posting_scopes + (
            missing_metrics_scopes if request_metrics_scopes else []
        )
        if missing_posting_scopes:
            reconsent_reason = 'posting'
        elif request_metrics_scopes and missing_metrics_scopes:
            reconsent_reason = 'metrics'
        else:
            reconsent_reason = None
        payload.update({
            'missing_scopes': missing_requested_scopes,
            'missing_posting_scopes': missing_posting_scopes,
            'missing_metrics_scopes': missing_metrics_scopes,
            'needs_reconsent': bool(missing_requested_scopes),
            'reconsent_reason': reconsent_reason,
            'posting_authorized': not missing_posting_scopes,
            'metrics_authorized': not missing_metrics_scopes,
            'metrics_scope_request_enabled': request_metrics_scopes,
        })
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
        'scope': ','.join(_tiktok_requested_scopes()),
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
    except ValueError:
        logger.error('TikTok credentials could not be read', exc_info=True)
        return jsonify({'error': 'TikTok connection could not be loaded'}), 500


@app.route('/api/articles/<int:article_id>/tiktok/publish', methods=['POST'])
def tiktok_publish_article(article_id):
    """Upload a generated video through TikTok's Direct Post API."""
    payload = request.get_json(silent=True) or {}
    try:
        result = publish_article_to_tiktok(article_id, payload)
        return jsonify(result), 202
    except TikTokPublishRequestError as error:
        return jsonify({'error': str(error)}), error.status_code
    except TikTokAPIError as error:
        return _tiktok_error_response(error)
    except Exception:
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
        status_data = refresh_tiktok_publish_status(article)
        return jsonify({'article': article.to_dict(), 'tiktok': status_data})
    except TikTokAPIError as error:
        article.tiktok_publish_error = _safe_tiktok_error_message(
            error,
            'TikTok status could not be refreshed',
        )
        db.session.commit()
        return _tiktok_error_response(error)


@app.route('/api/styles', methods=['GET'])
def list_styles_endpoint():
    """Return available visual style presets for UI consumption."""
    return jsonify({'styles': list_styles()})


@app.route('/api/generation-budget', methods=['GET'])
def generation_budget_endpoint():
    """Return cached provider balances and safe generation cost estimates."""
    response = jsonify(
        get_generation_budget(force=request.args.get('refresh') == '1')
    )
    # The server already maintains the 60-second cache. Do not let browsers or
    # shared proxies persist financial account metadata beyond the request.
    response.headers['Cache-Control'] = 'no-store'
    return response


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

# WSGI servers import this module rather than executing it as __main__. Every
# worker attempts startup, while discovery_web's file lock elects one owner.
if __name__ != '__main__':
    ensure_discovery_scheduler(app)

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  Clipper - Article to TikTok Video Generator")
    print("=" * 60)
    print("\n  Dashboard: http://localhost:5050")
    print("  API Base:  http://localhost:5050/api")
    print("\n" + "=" * 60 + "\n")

    # Flask's debug reloader executes this file twice. Only the serving child
    # should own the daily discovery scheduler.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        ensure_discovery_scheduler(app)
    app.run(host='0.0.0.0', port=5050, debug=True)

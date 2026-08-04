"""Database models for Clipper."""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def find_matching_hook_index(hook_variants, scenes):
    """Return the variant whose text exactly matches scene one's speech.

    Whitespace at either edge is ignored, but this intentionally does not do
    fuzzy matching: attribution should remain unknown when the stored script
    is not one of the generated variants.
    """
    if not isinstance(hook_variants, list) or not isinstance(scenes, list) or not scenes:
        return None
    first_scene = scenes[0]
    if not isinstance(first_scene, dict):
        return None
    first_speech = first_scene.get("speech")
    if not isinstance(first_speech, str) or not first_speech.strip():
        return None
    normalized_speech = first_speech.strip()
    for index, variant in enumerate(hook_variants):
        if isinstance(variant, str) and variant.strip() == normalized_speech:
            return index
    return None


def valid_hook_index(value, hook_variants):
    """Return a strict in-range hook index, otherwise ``None``."""
    if type(value) is not int or not isinstance(hook_variants, list):
        return None
    return value if 0 <= value < len(hook_variants) else None


class Article(db.Model):
    """Scraped article from the browser."""

    __tablename__ = 'articles'

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(2048), unique=True, nullable=False)
    title = db.Column(db.String(512), nullable=False)
    content = db.Column(db.Text, nullable=False)
    hero_image = db.Column(db.String(2048), nullable=True)
    site_name = db.Column(db.String(256), nullable=True)
    viral_score = db.Column(db.Float, nullable=True)

    # Status tracking
    status = db.Column(db.String(50), default='scraped')

    # Timestamps
    scraped_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    summarized_at = db.Column(db.DateTime, nullable=True)
    video_generated_at = db.Column(db.DateTime, nullable=True)
    carousel_generated_at = db.Column(db.DateTime, nullable=True)

    # Summary fields (populated after AI processing)
    tldr = db.Column(db.Text, nullable=True)
    bullets = db.Column(db.Text, nullable=True)  # JSON array stored as text
    video_script = db.Column(db.Text, nullable=True)
    hashtags = db.Column(db.Text, nullable=True)  # JSON array of 3 hashtags
    cover_line = db.Column(db.String(128), nullable=True)
    cta_question = db.Column(db.String(512), nullable=True)
    search_caption = db.Column(db.Text, nullable=True)
    series_lane = db.Column(db.String(32), nullable=True)

    # Engagement metadata (populated by summarizer for story-shaped video gen)
    scenes = db.Column(db.Text, nullable=True)  # JSON array of {speech, visual, emotion}
    hook_variants = db.Column(db.Text, nullable=True)  # JSON array of 3 alt opening lines
    best_hook_index = db.Column(db.Integer, nullable=True)
    hook_index_used = db.Column(db.Integer, nullable=True)
    dominant_emotion = db.Column(db.String(32), nullable=True)
    style = db.Column(db.String(32), nullable=True)  # visual_styles key
    color_intensity = db.Column(db.String(16), nullable=True)
    visual_sources = db.Column(db.Text, nullable=True)  # JSON audit record per scene

    # Video output
    video_path = db.Column(db.String(512), nullable=True)
    # Unique ownership token for one background render attempt. This prevents
    # a timed-out worker from overwriting a later retry that is still running.
    video_generation_token = db.Column(db.String(64), nullable=True)

    # TikTok Direct Post state
    tiktok_publish_id = db.Column(db.String(256), nullable=True)
    tiktok_publish_status = db.Column(db.String(64), nullable=True)
    tiktok_publish_error = db.Column(db.Text, nullable=True)
    tiktok_published_at = db.Column(db.DateTime, nullable=True)
    # Legacy nullable columns retained because Clipper's SQLite migration is
    # additive-only. New releases never write approval requests; startup
    # maintenance clears old rows safely.
    tiktok_approval_message_id = db.Column(db.String(64), nullable=True)
    tiktok_approval_requested_at = db.Column(db.DateTime, nullable=True)
    pending_publish_request = db.Column(db.Text, nullable=True)

    # Carousel output
    carousel_dir = db.Column(db.String(512), nullable=True)   # e.g. "42"
    carousel_audio = db.Column(db.String(512), nullable=True)  # e.g. "voiceover.wav"

    # Substack companion post (long-form, conversational)
    substack_post = db.Column(db.Text, nullable=True)

    def to_dict(self, include_full_content=False):
        """Convert to dictionary for JSON response.

        Args:
            include_full_content: If True, include full article content.
                                 If False, include only a preview (for list views).
        """
        import json

        result = {
            'id': self.id,
            'url': self.url,
            'title': self.title,
            'content': self.content[:500] + '...' if len(self.content) > 500 else self.content,
            'hero_image': self.hero_image,
            'site_name': self.site_name,
            'viral_score': self.viral_score,
            'status': self.status,
            'scraped_at': self.scraped_at.isoformat() if self.scraped_at else None,
            'summarized_at': self.summarized_at.isoformat() if self.summarized_at else None,
            'video_generated_at': self.video_generated_at.isoformat() if self.video_generated_at else None,
            'carousel_generated_at': self.carousel_generated_at.isoformat() if self.carousel_generated_at else None,
            'tldr': self.tldr,
            'bullets': json.loads(self.bullets) if self.bullets else None,
            'video_script': self.video_script,
            'hashtags': json.loads(self.hashtags) if self.hashtags else None,
            'cover_line': self.cover_line,
            'cta_question': self.cta_question,
            'search_caption': self.search_caption,
            'series_lane': self.series_lane,
            'scenes': json.loads(self.scenes) if self.scenes else None,
            'hook_variants': json.loads(self.hook_variants) if self.hook_variants else None,
            'best_hook_index': self.best_hook_index,
            'hook_index_used': self.hook_index_used,
            'dominant_emotion': self.dominant_emotion,
            'style': self.style,
            'color_intensity': self.color_intensity or 'vivid',
            'visual_sources': json.loads(self.visual_sources) if self.visual_sources else None,
            'video_path': self.video_path,
            'tiktok_publish_id': self.tiktok_publish_id,
            'tiktok_publish_status': self.tiktok_publish_status,
            'tiktok_publish_error': self.tiktok_publish_error,
            'tiktok_published_at': self.tiktok_published_at.isoformat() if self.tiktok_published_at else None,
            'platform_posts': [post.to_dict() for post in self.platform_posts],
            'carousel_dir': self.carousel_dir,
            'carousel_audio': self.carousel_audio,
            'substack_post': self.substack_post,
            'video_metrics': self.video_metrics.to_dict() if self.video_metrics else None,
        }

        if include_full_content:
            result['full_content'] = self.content

        return result


class PlatformPost(db.Model):
    """One durable publish attempt/state per article and destination."""

    __tablename__ = 'platform_posts'
    __table_args__ = (
        db.UniqueConstraint(
            'article_id',
            'platform',
            name='uq_platform_posts_article_platform',
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(
        db.Integer,
        db.ForeignKey('articles.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )
    platform = db.Column(db.String(32), nullable=False, index=True)
    external_id = db.Column(db.String(512), nullable=True)
    status = db.Column(db.String(64), nullable=False, default='QUEUED')
    error = db.Column(db.Text, nullable=True)
    permalink = db.Column(db.String(2048), nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    article = db.relationship(
        'Article',
        backref=db.backref(
            'platform_posts',
            cascade='all, delete-orphan',
            order_by='PlatformPost.platform',
        ),
    )

    def to_dict(self):
        return {
            'platform': self.platform,
            'status': self.status,
            'external_id': self.external_id,
            'error': self.error,
            'permalink': self.permalink,
            'published_at': self.published_at.isoformat() if self.published_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class PublisherAccount(db.Model):
    """Encrypted OAuth credentials for non-TikTok publishing providers."""

    __tablename__ = 'publisher_accounts'

    id = db.Column(db.Integer, primary_key=True)
    platform = db.Column(db.String(32), unique=True, nullable=False, index=True)
    external_user_id = db.Column(db.String(256), nullable=True)
    username = db.Column(db.String(256), nullable=True)
    access_token_encrypted = db.Column(db.Text, nullable=False)
    refresh_token_encrypted = db.Column(db.Text, nullable=True)
    scope = db.Column(db.String(1024), nullable=True)
    access_token_expires_at = db.Column(db.DateTime, nullable=True)
    refresh_token_expires_at = db.Column(db.DateTime, nullable=True)
    connected_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def to_public_dict(self):
        """Serialize only safe metadata; encrypted token fields stay private."""
        return {
            'connected': True,
            'external_user_id': self.external_user_id,
            'username': self.username,
            'scope': self.scope.split() if self.scope else [],
            'access_token_expires_at': (
                self.access_token_expires_at.isoformat()
                if self.access_token_expires_at else None
            ),
            'refresh_token_expires_at': (
                self.refresh_token_expires_at.isoformat()
                if self.refresh_token_expires_at else None
            ),
            'connected_at': self.connected_at.isoformat() if self.connected_at else None,
        }


class VideoMetrics(db.Model):
    """Latest performance snapshot for a TikTok video created from an article.

    TikTok's Display API exposes view and engagement counts, but it does not
    currently expose watch time. ``watch_time`` is therefore nullable so the
    schema is ready if/when that metric becomes available without inventing a
    value in the meantime.
    """

    __tablename__ = 'video_metrics'

    id = db.Column(db.Integer, primary_key=True)
    article_id = db.Column(
        db.Integer,
        db.ForeignKey('articles.id', ondelete='CASCADE'),
        nullable=False,
        unique=True,
        index=True,
    )
    tiktok_video_id = db.Column(db.String(128), nullable=False, unique=True, index=True)
    views = db.Column(db.BigInteger, nullable=False, default=0)
    likes = db.Column(db.BigInteger, nullable=False, default=0)
    comments = db.Column(db.BigInteger, nullable=False, default=0)
    shares = db.Column(db.BigInteger, nullable=False, default=0)
    watch_time = db.Column(db.Float, nullable=True)
    fetched_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    article = db.relationship(
        'Article',
        backref=db.backref(
            'video_metrics',
            uselist=False,
            cascade='all, delete-orphan',
            single_parent=True,
        ),
    )

    def to_dict(self):
        return {
            'article_id': self.article_id,
            'tiktok_video_id': self.tiktok_video_id,
            'views': self.views,
            'likes': self.likes,
            'comments': self.comments,
            'shares': self.shares,
            'watch_time': self.watch_time,
            'fetched_at': self.fetched_at.isoformat() if self.fetched_at else None,
        }


class TikTokAccount(db.Model):
    """The single TikTok creator account connected to this Clipper instance."""

    __tablename__ = 'tiktok_accounts'

    id = db.Column(db.Integer, primary_key=True)
    open_id = db.Column(db.String(128), unique=True, nullable=False)
    access_token_encrypted = db.Column(db.Text, nullable=False)
    refresh_token_encrypted = db.Column(db.Text, nullable=False)
    scope = db.Column(db.String(512), nullable=True)
    access_token_expires_at = db.Column(db.DateTime, nullable=False)
    refresh_token_expires_at = db.Column(db.DateTime, nullable=False)

    creator_username = db.Column(db.String(128), nullable=True)
    creator_nickname = db.Column(db.String(256), nullable=True)
    creator_avatar_url = db.Column(db.String(2048), nullable=True)
    connected_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_public_dict(self):
        """Return connection metadata without ever exposing OAuth tokens."""
        return {
            'connected': True,
            'open_id': self.open_id,
            'scope': self.scope.split(',') if self.scope else [],
            'creator_username': self.creator_username,
            'creator_nickname': self.creator_nickname,
            'creator_avatar_url': self.creator_avatar_url,
            'access_token_expires_at': self.access_token_expires_at.isoformat() if self.access_token_expires_at else None,
            'connected_at': self.connected_at.isoformat() if self.connected_at else None,
        }

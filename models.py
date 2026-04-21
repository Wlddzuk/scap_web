"""Database models for Clipper."""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class Article(db.Model):
    """Scraped article from the browser."""

    __tablename__ = 'articles'

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(2048), unique=True, nullable=False)
    title = db.Column(db.String(512), nullable=False)
    content = db.Column(db.Text, nullable=False)
    hero_image = db.Column(db.String(2048), nullable=True)
    site_name = db.Column(db.String(256), nullable=True)

    # Status tracking
    status = db.Column(db.String(50), default='scraped')

    # Timestamps
    scraped_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    summarized_at = db.Column(db.DateTime, nullable=True)
    video_generated_at = db.Column(db.DateTime, nullable=True)

    # Summary fields (populated after AI processing)
    tldr = db.Column(db.Text, nullable=True)
    bullets = db.Column(db.Text, nullable=True)  # JSON array stored as text
    video_script = db.Column(db.Text, nullable=True)
    hashtags = db.Column(db.Text, nullable=True)  # JSON array of 5 hashtags

    # Engagement metadata (populated by summarizer for story-shaped video gen)
    scenes = db.Column(db.Text, nullable=True)  # JSON array of {speech, visual, emotion}
    hook_variants = db.Column(db.Text, nullable=True)  # JSON array of 3 alt opening lines
    dominant_emotion = db.Column(db.String(32), nullable=True)
    style = db.Column(db.String(32), nullable=True)  # visual_styles key

    # Video output
    video_path = db.Column(db.String(512), nullable=True)

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
            'status': self.status,
            'scraped_at': self.scraped_at.isoformat() if self.scraped_at else None,
            'summarized_at': self.summarized_at.isoformat() if self.summarized_at else None,
            'video_generated_at': self.video_generated_at.isoformat() if self.video_generated_at else None,
            'tldr': self.tldr,
            'bullets': json.loads(self.bullets) if self.bullets else None,
            'video_script': self.video_script,
            'hashtags': json.loads(self.hashtags) if self.hashtags else None,
            'scenes': json.loads(self.scenes) if self.scenes else None,
            'hook_variants': json.loads(self.hook_variants) if self.hook_variants else None,
            'dominant_emotion': self.dominant_emotion,
            'style': self.style,
            'video_path': self.video_path,
            'substack_post': self.substack_post,
        }

        if include_full_content:
            result['full_content'] = self.content

        return result

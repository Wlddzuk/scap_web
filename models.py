"""Database models for Article Scraper MVP."""

from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

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
    status = db.Column(db.String(50), default='scraped')  # scraped, summarizing, summarized, generating_video, video_done, failed
    
    # Timestamps
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    summarized_at = db.Column(db.DateTime, nullable=True)
    video_generated_at = db.Column(db.DateTime, nullable=True)
    
    # Summary fields (populated after AI processing)
    tldr = db.Column(db.Text, nullable=True)
    bullets = db.Column(db.Text, nullable=True)  # JSON array stored as text
    video_script = db.Column(db.Text, nullable=True)
    
    # Video output
    video_path = db.Column(db.String(512), nullable=True)
    
    def to_dict(self):
        """Convert to dictionary for JSON response."""
        import json
        return {
            'id': self.id,
            'url': self.url,
            'title': self.title,
            'content': self.content[:500] + '...' if len(self.content) > 500 else self.content,
            'full_content': self.content,
            'hero_image': self.hero_image,
            'site_name': self.site_name,
            'status': self.status,
            'scraped_at': self.scraped_at.isoformat() if self.scraped_at else None,
            'summarized_at': self.summarized_at.isoformat() if self.summarized_at else None,
            'video_generated_at': self.video_generated_at.isoformat() if self.video_generated_at else None,
            'tldr': self.tldr,
            'bullets': json.loads(self.bullets) if self.bullets else None,
            'video_script': self.video_script,
            'video_path': self.video_path,
        }

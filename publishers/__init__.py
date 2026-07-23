"""Publishing adapters for Clipper's supported short-video platforms."""

from .base import PublishResult, Publisher, PublisherError
from .facebook import FacebookPublisher
from .instagram import InstagramPublisher
from .tiktok import TikTokPublisher
from .youtube import YouTubePublisher

__all__ = [
    'FacebookPublisher',
    'InstagramPublisher',
    'PublishResult',
    'Publisher',
    'PublisherError',
    'TikTokPublisher',
    'YouTubePublisher',
]

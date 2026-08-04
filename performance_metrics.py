"""TikTok performance snapshots and article-to-video matching for Clipper."""

from __future__ import annotations

import logging
import os
import re
import atexit
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Callable

from models import Article, VideoMetrics, db
from tiktok_service import list_user_videos, query_user_stats

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - production hosts are Linux/macOS.
    fcntl = None

_SCHEDULER_LOCK = Lock()
_scheduler = None
_scheduler_owner = None


def _normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _public_post_ids(status_data: dict) -> list[str]:
    """Handle TikTok's documented misspelling plus the corrected spelling."""
    raw_ids = (
        status_data.get("publicaly_available_post_id")
        or status_data.get("publicly_available_post_id")
        or []
    )
    if not isinstance(raw_ids, list):
        return []
    return [str(value) for value in raw_ids if value not in (None, "")]


def record_public_post_id(article: Article, status_data: dict) -> VideoMetrics | None:
    """Attach TikTok's public post id to an article as soon as it is available."""
    post_ids = _public_post_ids(status_data)
    if not post_ids:
        return None

    post_id = post_ids[0]
    existing = VideoMetrics.query.filter_by(article_id=article.id).first()
    if existing is None:
        existing = VideoMetrics(article_id=article.id, tiktok_video_id=post_id)
        db.session.add(existing)
    elif existing.tiktok_video_id != post_id:
        # A post id is a mapping, not a measurement. If TikTok corrects the id,
        # discard counters belonging to the old video and wait for a real
        # ``video.list`` refresh before exposing this row as performance data.
        existing.tiktok_video_id = post_id
        existing.views = 0
        existing.likes = 0
        existing.comments = 0
        existing.shares = 0
        existing.watch_time = None
        existing.fetched_at = None
    return existing


def _fetch_recent_videos(access_token: str) -> list[dict]:
    try:
        max_pages = max(1, min(20, int(os.getenv("TIKTOK_METRICS_MAX_PAGES", "5"))))
    except ValueError:
        max_pages = 5

    videos: list[dict] = []
    cursor = None
    seen_cursors = set()
    for _ in range(max_pages):
        page = list_user_videos(access_token, max_count=20, cursor=cursor)
        videos.extend(item for item in (page.get("videos") or []) if isinstance(item, dict))
        if not page.get("has_more"):
            break
        next_cursor = page.get("cursor")
        if next_cursor is None or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return videos


def _match_video(
    article: Article,
    videos: list[dict],
    used_video_ids: set[str],
    caption_builder: Callable[[Article], str] | None,
) -> dict | None:
    expected_caption = _normalized(
        caption_builder(article) if caption_builder else article.title
    )
    expected_title = _normalized(article.title)
    candidates = []
    for video in videos:
        video_id = str(video.get("id") or "")
        if not video_id or video_id in used_video_ids:
            continue
        description = _normalized(video.get("video_description"))
        title = _normalized(video.get("title"))
        exact_caption = expected_caption and expected_caption in {description, title}
        title_match = expected_title and (
            description.startswith(expected_title) or title.startswith(expected_title)
        )
        if exact_caption or title_match:
            candidates.append(video)

    if not candidates:
        return None

    published_timestamp = 0
    if article.tiktok_published_at:
        value = article.tiktok_published_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        published_timestamp = int(value.timestamp())
    if published_timestamp:
        return min(
            candidates,
            key=lambda video: abs(int(video.get("create_time") or 0) - published_timestamp),
        )
    return max(candidates, key=lambda video: int(video.get("create_time") or 0))


def refresh_video_metrics(
    access_token: str,
    *,
    caption_builder: Callable[[Article], str] | None = None,
) -> dict:
    """Refresh the latest per-video counters plus account totals.

    Must run inside an application context. The routine is idempotent and
    updates one snapshot row per article. TikTok does not expose watch time in
    the Display API, so ``watch_time`` remains ``None``.
    """
    videos = _fetch_recent_videos(access_token)
    video_by_id = {
        str(video.get("id")): video
        for video in videos
        if video.get("id") not in (None, "")
    }
    published = Article.query.filter_by(tiktok_publish_status="PUBLISH_COMPLETE").all()
    used_video_ids = {
        metrics.tiktok_video_id
        for metrics in VideoMetrics.query.all()
        if metrics.tiktok_video_id
    }

    updated = 0
    now = datetime.now(timezone.utc)
    for article in published:
        metrics = VideoMetrics.query.filter_by(article_id=article.id).first()
        video = video_by_id.get(metrics.tiktok_video_id) if metrics else None
        if video is None and metrics is None:
            video = _match_video(article, videos, used_video_ids, caption_builder)
            if video:
                metrics = VideoMetrics(
                    article_id=article.id,
                    tiktok_video_id=str(video["id"]),
                )
                db.session.add(metrics)
                used_video_ids.add(metrics.tiktok_video_id)
        if metrics is None or video is None:
            continue

        metrics.views = int(video.get("view_count") or 0)
        metrics.likes = int(video.get("like_count") or 0)
        metrics.comments = int(video.get("comment_count") or 0)
        metrics.shares = int(video.get("share_count") or 0)
        metrics.fetched_at = now
        updated += 1

    db.session.commit()
    account_stats = query_user_stats(access_token)
    logger.info(
        "TikTok metrics refreshed for %d/%d published article(s)",
        updated,
        len(published),
    )
    return {
        "updated": updated,
        "available_videos": len(videos),
        "account": account_stats,
    }


def _release_scheduler() -> None:
    global _scheduler, _scheduler_owner
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.debug("TikTok metrics scheduler already stopped", exc_info=True)
        _scheduler = None
    if _scheduler_owner is not None:
        try:
            if fcntl is not None:
                fcntl.flock(_scheduler_owner.fileno(), fcntl.LOCK_UN)
        finally:
            _scheduler_owner.close()
            _scheduler_owner = None


def ensure_metrics_scheduler(flask_app, refresh_callback: Callable[[], None]) -> bool:
    """Start one metrics refresher across the Flask reloader/Gunicorn workers."""
    global _scheduler, _scheduler_owner
    if flask_app.config.get("TESTING"):
        return False
    if os.getenv("TIKTOK_METRICS_ENABLED", "true").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return False
    if _scheduler is not None:
        return True

    with _SCHEDULER_LOCK:
        if _scheduler is not None:
            return True
        lock_path = Path(flask_app.instance_path) / "tiktok-metrics-scheduler.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        owner = lock_path.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(owner.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                owner.close()
                return False

        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            try:
                interval_hours = max(
                    1,
                    min(168, int(os.getenv("TIKTOK_METRICS_INTERVAL_HOURS", "6"))),
                )
            except ValueError:
                interval_hours = 6

            def run_job():
                with flask_app.app_context():
                    try:
                        refresh_callback()
                    except Exception:
                        logger.error("Scheduled TikTok metrics refresh failed", exc_info=True)
                    finally:
                        db.session.remove()

            scheduler = BackgroundScheduler(timezone=timezone.utc, daemon=True)
            scheduler.add_job(
                run_job,
                trigger="interval",
                hours=interval_hours,
                id="clipper-tiktok-metrics",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1),
            )
            scheduler.start()
            _scheduler = scheduler
            _scheduler_owner = owner
            logger.info("TikTok metrics scheduled every %d hour(s)", interval_hours)
            return True
        except Exception:
            if fcntl is not None:
                fcntl.flock(owner.fileno(), fcntl.LOCK_UN)
            owner.close()
            logger.error("Could not start TikTok metrics scheduler", exc_info=True)
            return False


atexit.register(_release_scheduler)

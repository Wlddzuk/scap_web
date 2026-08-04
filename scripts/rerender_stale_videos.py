#!/usr/bin/env python3
"""Safely re-render pre-caption/oversized Clipper videos.

The command is a dry run unless ``--execute`` is supplied. A replacement is
committed to SQLite only after a complete render exists and, when needed, an
ffmpeg CRF pass has brought it under the configured Discord upload limit.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)
(ROOT / "instance").mkdir(parents=True, exist_ok=True)

from app import app  # noqa: E402
from models import Article, db, find_matching_hook_index  # noqa: E402
from video_generator import generate_video  # noqa: E402


DEFAULT_STALE_BEFORE = "2026-03-12T00:00:00"
VIDEOS_DIR = (ROOT / "static" / "videos").resolve()


def parse_cutoff(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def fit_discord_limit(video_path: Path, max_bytes: int, timeout_seconds: int) -> bool:
    """Try progressively smaller CRFs without ever leaving a partial output."""
    if video_path.stat().st_size <= max_bytes:
        return True

    candidate_path = video_path.with_name(f".{video_path.stem}.discord.mp4")
    try:
        for crf in (28, 30, 32, 34):
            candidate_path.unlink(missing_ok=True)
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(video_path),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart",
                str(candidate_path),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    timeout=timeout_seconds,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except (FileNotFoundError, subprocess.SubprocessError) as exc:
                print(f"  ffmpeg compression failed at CRF {crf}: {exc}")
                return False

            size = candidate_path.stat().st_size
            print(f"  CRF {crf}: {size / (1024 * 1024):.1f} MB")
            if size <= max_bytes:
                candidate_path.replace(video_path)
                return True
        return False
    finally:
        candidate_path.unlink(missing_ok=True)


def candidate_articles(cutoff: datetime, article_ids: list[int] | None) -> list[Article]:
    query = Article.query.filter(
        Article.video_path.isnot(None),
        Article.video_script.isnot(None),
    )
    if article_ids:
        query = query.filter(Article.id.in_(article_ids))
    else:
        query = query.filter(
            (Article.video_generated_at.is_(None))
            | (Article.video_generated_at < cutoff)
        )
    return query.order_by(Article.id.asc()).all()


def hook_setting(value: str) -> bool | None:
    return {"env": None, "on": True, "off": False}[value]


def safe_video_path(filename: str) -> Path:
    path = (VIDEOS_DIR / filename).resolve()
    if os.path.commonpath([str(VIDEOS_DIR), str(path)]) != str(VIDEOS_DIR):
        raise ValueError("video path is outside static/videos")
    return path


def commit_replacement(
    article: Article,
    new_path: Path,
    old_path: Path,
    keep_old: bool,
) -> float:
    """Commit a verified replacement before best-effort old-file cleanup."""
    size_mb = new_path.stat().st_size / (1024 * 1024)
    article.video_path = new_path.name
    article.video_generated_at = datetime.now(timezone.utc)
    try:
        hook_variants = (
            json.loads(article.hook_variants)
            if getattr(article, "hook_variants", None)
            else []
        )
        scenes = (
            json.loads(article.scenes)
            if getattr(article, "scenes", None)
            else []
        )
        article.hook_index_used = find_matching_hook_index(
            hook_variants,
            scenes,
        )
    except (TypeError, ValueError):
        article.hook_index_used = None
    article.status = "video_done"
    db.session.commit()

    if not keep_old:
        try:
            if old_path.resolve() != new_path.resolve():
                old_path.unlink(missing_ok=True)
        except OSError as exc:
            # The database already points at a complete replacement. Failure to
            # remove the superseded file must never delete or roll back that file.
            print(f"  warning: could not remove old video {old_path.name}: {exc}")
    return size_mb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        default=DEFAULT_STALE_BEFORE,
        help=f"ISO cutoff used when no article IDs are supplied (default: {DEFAULT_STALE_BEFORE})",
    )
    parser.add_argument("--article-id", type=int, action="append", dest="article_ids")
    parser.add_argument("--execute", action="store_true", help="perform renders; otherwise list only")
    parser.add_argument("--keep-old", action="store_true", help="keep superseded MP4 files")
    parser.add_argument(
        "--image-source",
        choices=("ai", "stock", "mixed"),
        default="ai",
    )
    parser.add_argument("--video-hook", choices=("env", "on", "off"), default="env")
    parser.add_argument("--max-size-mb", type=float, default=25.0)
    parser.add_argument("--ffmpeg-timeout", type=int, default=900)
    args = parser.parse_args()

    cutoff = parse_cutoff(args.before)
    max_bytes = int(max(1.0, args.max_size_mb) * 1024 * 1024)

    with app.app_context():
        articles = candidate_articles(cutoff, args.article_ids)
        if not articles:
            print("No stale videos matched.")
            return 0

        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"{mode}: {len(articles)} stale video(s)")
        for article in articles:
            try:
                old_path = safe_video_path(article.video_path)
            except ValueError as exc:
                print(f"[{article.id}] skipped: {exc}")
                continue
            old_size = old_path.stat().st_size if old_path.exists() else 0
            generated = article.video_generated_at.isoformat() if article.video_generated_at else "unknown"
            print(
                f"[{article.id}] {article.title[:70]} | {generated} | "
                f"{old_size / (1024 * 1024):.1f} MB"
            )
            if not args.execute:
                continue

            new_path = None
            try:
                scenes = json.loads(article.scenes) if article.scenes else None
                new_path = Path(
                    generate_video(
                        article_id=article.id,
                        title=article.title,
                        script=article.video_script,
                        image_source=args.image_source,
                        scenes=scenes,
                        style_key=article.style or None,
                        emotion=article.dominant_emotion,
                        use_video_hook=hook_setting(args.video_hook),
                        cover_line=getattr(article, "cover_line", None),
                        series_lane=getattr(article, "series_lane", None),
                        hero_image=getattr(article, "hero_image", None),
                    )
                ).resolve()
                if os.path.commonpath([str(VIDEOS_DIR), str(new_path)]) != str(VIDEOS_DIR):
                    raise ValueError("generator returned a path outside static/videos")
                if not fit_discord_limit(new_path, max_bytes, args.ffmpeg_timeout):
                    size_mb = new_path.stat().st_size / (1024 * 1024)
                    print(
                        f"  replacement remains {size_mb:.1f} MB, above "
                        f"{args.max_size_mb:.1f} MB; database left unchanged"
                    )
                    new_path.unlink(missing_ok=True)
                    continue

                size_mb = commit_replacement(
                    article,
                    new_path,
                    old_path,
                    args.keep_old,
                )
                print(f"  replaced with {new_path.name} ({size_mb:.1f} MB)")
            except Exception as exc:
                db.session.rollback()
                if new_path is not None:
                    new_path.unlink(missing_ok=True)
                print(f"  failed; database left unchanged: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

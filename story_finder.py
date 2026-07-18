"""Discover and score science stories for Clipper's automated video pipeline."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup

from app import app, db, scrape_url_content
from models import Article
from summarizer import summarize_article
from video_generator import generate_video, get_groq_client

logger = logging.getLogger(__name__)

FEEDS = {
    "ScienceDaily": "https://www.sciencedaily.com/rss/all.xml",
    "Phys.org": "https://phys.org/rss-feed/",
    "Live Science": "https://www.livescience.com/feeds/all",
    "EurekAlert": "https://www.eurekalert.org/rss.xml",
}
REDDIT_URL = "https://www.reddit.com/r/science/top.json?t=day&limit=25"
USER_AGENT = "ClipperStoryFinder/1.0 (+https://github.com/clipper; science-video-discovery)"
NETWORK_TIMEOUT = 15
SCORING_TIMEOUT = 60
MAX_FEED_ITEMS = 10
MAX_REDDIT_ITEMS = 10
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


@dataclass
class StoryCandidate:
    """A story headline and metadata collected from a discovery source."""

    title: str
    url: str
    source: str
    summary: str = ""
    published: str = ""
    reddit_score: int = 0
    viral_score: Optional[float] = None
    score_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _request_with_retry(url: str, **kwargs) -> requests.Response:
    """GET a URL with a hard timeout and exactly one retry."""
    kwargs.setdefault("timeout", NETWORK_TIMEOUT)
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("User-Agent", USER_AGENT)
    last_error = None
    for attempt in range(2):
        try:
            response = requests.get(url, headers=headers, **kwargs)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise last_error


def _plain_text(value: str, limit: int = 500) -> str:
    """Collapse feed HTML into a short, plain-text scoring excerpt."""
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _canonical_url(url: str) -> str:
    """Normalize URL details that should not make two stories distinct."""
    try:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return ""
        hostname = parts.hostname.lower()
        if parts.port and not (
            (parts.scheme == "http" and parts.port == 80)
            or (parts.scheme == "https" and parts.port == 443)
        ):
            hostname = f"{hostname}:{parts.port}"
        path = parts.path.rstrip("/") or "/"
        filtered_query = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in TRACKING_QUERY_KEYS
            and not key.lower().startswith(TRACKING_QUERY_PREFIXES)
        ]
        return urlunsplit(
            (parts.scheme.lower(), hostname, path, urlencode(filtered_query), "")
        )
    except (TypeError, ValueError):
        return ""


def _existing_url_keys() -> set[str]:
    with app.app_context():
        return {
            key
            for (url,) in db.session.query(Article.url).all()
            if (key := _canonical_url(url))
        }


def _feed_candidates(source: str, feed_url: str) -> list[StoryCandidate]:
    """Fetch one RSS feed. Errors are contained to this feed."""
    try:
        response = _request_with_retry(feed_url)
        parsed = feedparser.parse(response.content)
        if not parsed.entries:
            detail = getattr(parsed, "bozo_exception", "no entries")
            raise ValueError(f"RSS parse returned no entries: {detail}")

        candidates = []
        for entry in parsed.entries[:MAX_FEED_ITEMS]:
            title = _plain_text(entry.get("title", ""), limit=300)
            url = (entry.get("link") or entry.get("id") or "").strip()
            if not title or not _canonical_url(url):
                continue
            candidates.append(
                StoryCandidate(
                    title=title,
                    url=url,
                    source=source,
                    summary=_plain_text(
                        entry.get("summary") or entry.get("description") or ""
                    ),
                    published=str(
                        entry.get("published") or entry.get("updated") or ""
                    ),
                )
            )
        logger.info("[Discovery] %s returned %d candidates", source, len(candidates))
        return candidates
    except Exception as exc:
        logger.warning("[Discovery] Feed %s failed after retry: %s", source, exc)
        return []


def _reddit_candidates() -> list[StoryCandidate]:
    """Fetch r/science top-of-day and retain each post's linked article URL."""
    try:
        response = _request_with_retry(
            REDDIT_URL,
            headers={"User-Agent": USER_AGENT},
        )
        children = response.json().get("data", {}).get("children", [])
        candidates = []
        for child in children[:MAX_REDDIT_ITEMS]:
            data = child.get("data", {})
            url = (
                data.get("url_overridden_by_dest") or data.get("url") or ""
            ).strip()
            key = _canonical_url(url)
            host = (urlsplit(key).hostname or "").lower() if key else ""
            if not key or host.endswith("reddit.com") or data.get("is_self"):
                continue
            title = _plain_text(data.get("title", ""), limit=300)
            if not title:
                continue
            candidates.append(
                StoryCandidate(
                    title=title,
                    url=url,
                    source="Reddit r/science",
                    summary=_plain_text(data.get("selftext", "")),
                    published=str(data.get("created_utc", "")),
                    reddit_score=int(data.get("score") or 0),
                )
            )
        logger.info("[Discovery] Reddit returned %d linked candidates", len(candidates))
        return candidates
    except Exception as exc:
        logger.warning("[Discovery] Reddit failed after retry: %s", exc)
        return []


def collect_candidates() -> list[StoryCandidate]:
    """Collect unseen feed and Reddit candidates, deduplicated within the run."""
    existing = _existing_url_keys()
    seen = set(existing)
    unseen = []

    raw_candidates = []
    for source, feed_url in FEEDS.items():
        raw_candidates.extend(_feed_candidates(source, feed_url))
    raw_candidates.extend(_reddit_candidates())

    for candidate in raw_candidates:
        key = _canonical_url(candidate.url)
        if not key or key in seen:
            continue
        seen.add(key)
        unseen.append(candidate)

    logger.info(
        "[Discovery] %d unseen candidates after database/run deduplication",
        len(unseen),
    )
    return unseen


def _parse_scores(payload: str, candidates: list[StoryCandidate]) -> dict[int, dict]:
    """Strictly validate the scorer's complete JSON response."""
    data = json.loads(payload)
    if not isinstance(data, dict) or set(data) != {"scores"}:
        raise ValueError("Expected a JSON object containing only 'scores'")
    scores = data["scores"]
    if not isinstance(scores, list) or len(scores) != len(candidates):
        actual = len(scores) if isinstance(scores, list) else "non-list"
        raise ValueError(
            f"Scorer returned {actual} scores for {len(candidates)} candidates"
        )

    parsed = {}
    for item in scores:
        if not isinstance(item, dict) or set(item) != {"id", "score", "reason"}:
            raise ValueError("Each score needs exactly id, score, and reason")
        item_id = item["id"]
        score = item["score"]
        reason = item["reason"]
        if isinstance(item_id, bool) or not isinstance(item_id, int):
            raise ValueError("Score id must be an integer")
        if item_id in parsed or not 0 <= item_id < len(candidates):
            raise ValueError("Score id is missing, duplicated, or out of range")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("Score must be numeric")
        if not 0 <= float(score) <= 100 or not isinstance(reason, str):
            raise ValueError("Score must be 0-100 and reason must be text")
        parsed[item_id] = {"score": float(score), "reason": reason.strip()[:300]}

    if set(parsed) != set(range(len(candidates))):
        raise ValueError("Scorer omitted one or more candidate ids")
    return parsed


def score_candidates(candidates: Iterable[StoryCandidate]) -> list[StoryCandidate]:
    """Score every candidate in one batched Groq request, retrying once."""
    candidates = list(candidates)
    if not candidates:
        return []

    client = get_groq_client()
    if not client:
        logger.error("[Discovery] GROQ_API_KEY is required for viral scoring")
        return []
    # Groq's SDK retries transport/rate-limit failures by default. Disable those
    # hidden retries so this function performs exactly the one explicit retry.
    scoring_client = (
        client.with_options(max_retries=0)
        if hasattr(client, "with_options")
        else client
    )

    compact_candidates = [
        {
            "id": index,
            "title": candidate.title,
            "source": candidate.source,
            "summary": candidate.summary[:350],
            "reddit_score": candidate.reddit_score,
        }
        for index, candidate in enumerate(candidates)
    ]
    system_prompt = (
        "You are the story editor for @60s.science2, a Gen-Z science TikTok "
        "channel. Score every supplied candidate from 0 to 100 using wow-factor, "
        "visual-ness, broad appeal, curiosity gap, and fit for a compelling "
        "60-second video. Return strict JSON only with exactly this schema: "
        '{"scores":[{"id":0,"score":87,"reason":"brief reason"}]}. '
        "Return every id exactly once. Do not add keys or markdown."
    )
    user_prompt = "Score this complete candidate batch:\n" + json.dumps(
        compact_candidates,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    last_error = None
    for attempt in range(2):
        try:
            response = scoring_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=4000,
                response_format={"type": "json_object"},
                timeout=SCORING_TIMEOUT,
            )
            parsed = _parse_scores(
                response.choices[0].message.content.strip(),
                candidates,
            )
            for index, candidate in enumerate(candidates):
                candidate.viral_score = parsed[index]["score"]
                candidate.score_reason = parsed[index]["reason"]
            return sorted(
                candidates,
                key=lambda candidate: candidate.viral_score or 0,
                reverse=True,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "[Discovery] Batched Groq scoring attempt %d/2 failed: %s",
                attempt + 1,
                exc,
            )
            if attempt == 0:
                time.sleep(1)

    logger.error("[Discovery] Scoring failed after one retry: %s", last_error)
    return []


def discover_and_score() -> list[StoryCandidate]:
    """Collect, deduplicate, score, and rank current science candidates."""
    return score_candidates(collect_candidates())


def format_shortlist(candidates: Iterable[StoryCandidate], limit: int = 8) -> str:
    """Format a compact Discord-safe ranked shortlist."""
    lines = []
    for index, candidate in enumerate(list(candidates)[:limit], start=1):
        score = candidate.viral_score if candidate.viral_score is not None else 0
        lines.append(
            f"{index}. **{score:.0f}/100** — {candidate.title[:120]} "
            f"_({candidate.source})_\n<{candidate.url}>"
        )
    return "\n".join(lines) or "No unseen candidates were available."


def _scrape_with_retry(url: str) -> dict:
    last_error = None
    for attempt in range(2):
        try:
            return scrape_url_content(url)
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1)
    raise last_error


def _process_candidate(candidate: StoryCandidate) -> dict:
    """Run one selected story through the existing persisted video pipeline."""
    article_id = None
    try:
        with app.app_context():
            existing = Article.query.filter_by(url=candidate.url).first()
            if existing:
                return {
                    "status": "skipped",
                    "url": candidate.url,
                    "title": candidate.title,
                    "article_id": existing.id,
                    "reason": "already exists",
                }

        scraped = _scrape_with_retry(candidate.url)
        if not scraped.get("content") or len(scraped["content"]) < 100:
            raise ValueError("Could not extract at least 100 characters of article content")

        with app.app_context():
            existing = Article.query.filter_by(url=scraped["url"]).first()
            if existing:
                return {
                    "status": "skipped",
                    "url": candidate.url,
                    "title": candidate.title,
                    "article_id": existing.id,
                    "reason": "already exists after redirect",
                }
            article = Article(
                url=scraped["url"],
                title=scraped["title"],
                content=scraped["content"],
                hero_image=scraped.get("hero_image"),
                site_name=scraped.get("site_name"),
                status="scraped",
                viral_score=candidate.viral_score,
            )
            db.session.add(article)
            db.session.commit()
            article_id = article.id

            summary = summarize_article(article.title, article.content)
            article.tldr = summary["tldr"]
            article.bullets = json.dumps(summary["bullets"])
            article.video_script = summary["video_script"]
            article.hashtags = json.dumps(summary.get("hashtags", []))

            # Engagement metadata (scene-based generation)
            from visual_styles import STYLES as VISUAL_STYLES
            scenes = summary.get("scenes") or []
            article.scenes = json.dumps(scenes) if scenes else None
            hook_variants = summary.get("hook_variants") or []
            article.hook_variants = json.dumps(hook_variants) if hook_variants else None
            article.dominant_emotion = summary.get("dominant_emotion") or None
            suggested = summary.get("suggested_style")
            if suggested and suggested in VISUAL_STYLES:
                article.style = suggested

            article.status = "summarized"
            article.summarized_at = datetime.now(timezone.utc)
            db.session.commit()
            title = article.title
            script = article.video_script
            article_scenes = scenes or None
            article_style = article.style
            article_emotion = article.dominant_emotion

        video_path = generate_video(
            article_id=article_id,
            title=title,
            script=script,
            image_source="ai",
            scenes=article_scenes,
            style_key=article_style,
            emotion=article_emotion,
        )

        with app.app_context():
            article = db.session.get(Article, article_id)
            article.video_path = Path(video_path).name
            article.status = "video_done"
            article.video_generated_at = datetime.now(timezone.utc)
            db.session.commit()
            return {
                "status": "video_done",
                "url": article.url,
                "title": article.title,
                "article_id": article.id,
                "video_path": article.video_path,
                "viral_score": article.viral_score,
            }
    except Exception as exc:
        logger.error(
            "[Discovery] Pipeline failed for %s: %s",
            candidate.url,
            exc,
            exc_info=True,
        )
        if article_id is not None:
            with app.app_context():
                article = db.session.get(Article, article_id)
                if article:
                    article.status = "failed"
                    db.session.commit()
        return {
            "status": "failed",
            "url": candidate.url,
            "title": candidate.title,
            "article_id": article_id,
            "error": str(exc)[:300],
        }


def run_discovery(
    top_n: int = 2,
    candidates: Optional[Iterable[StoryCandidate]] = None,
) -> dict:
    """Process the highest-scoring unseen candidates through Clipper."""
    try:
        top_n = max(1, int(top_n))
    except (TypeError, ValueError):
        top_n = 2

    ranked = list(candidates) if candidates is not None else discover_and_score()
    ranked = sorted(
        (candidate for candidate in ranked if candidate.viral_score is not None),
        key=lambda candidate: candidate.viral_score,
        reverse=True,
    )
    selected = ranked[:top_n]
    results = [_process_candidate(candidate) for candidate in selected]
    return {
        "candidates": [candidate.to_dict() for candidate in ranked],
        "selected": [candidate.to_dict() for candidate in selected],
        "results": results,
    }


if __name__ == "__main__":
    configured_top_n = os.getenv("DISCOVERY_TOP_N", "2")
    print(json.dumps(run_discovery(configured_top_n), indent=2))

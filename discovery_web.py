"""Web routes and process-local scheduling for science story discovery.

The shortlist is persisted as a small JSON document in Flask's instance
directory. That keeps GET requests useful when Gunicorn hands them to a
different worker than the one that performed discovery, without introducing a
new database table for transient candidates.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Iterator, Optional, TextIO

from flask import Blueprint, current_app, jsonify, request

try:  # fcntl is available on the Linux/macOS hosts Clipper supports.
    import fcntl
except ImportError:  # pragma: no cover - graceful fallback for local Windows dev.
    fcntl = None


logger = logging.getLogger(__name__)

discovery_bp = Blueprint("discovery", __name__, url_prefix="/api/discovery")

_STATE_LOCK = Lock()
_SCHEDULER_LOCK = Lock()
_scheduler = None
_scheduler_owner: Optional[TextIO] = None
_scheduler_retry_after = 0.0
_SCHEDULER_RETRY_SECONDS = 30.0

_PIPELINE_FAILURE_MESSAGES = {
    "scrape": "Scrape failed: the source article could not be read.",
    "summarize": "Summary failed: the story could not be summarized.",
    "render": "Video render failed: the video could not be created.",
}
_SCRAPE_FAILURE_MESSAGES = {
    "source_blocked": (
        "Scrape failed: the source blocked automated access and its feed "
        "summary was too short to use."
    ),
    "not_enough_text": "Scrape failed: the source did not contain enough readable text.",
}
_DISCOVERY_FAILURE_MESSAGES = {
    "scoring_unavailable": (
        "Stories were found, but the ranking service is temporarily unavailable. "
        "Please try again."
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "running": False,
        "candidates": [],
        "count": 0,
        "started_at": None,
        "updated_at": None,
        "error": None,
        "run_version": 0,
    }


def _state_path(flask_app) -> Path:
    configured = os.getenv("DISCOVERY_STATE_PATH", "").strip()
    path = Path(configured) if configured else Path(flask_app.instance_path) / "discovery.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _lock_path(flask_app, name: str) -> Path:
    state_path = _state_path(flask_app)
    return state_path.with_name(f"{state_path.name}.{name}.lock")


def _try_file_lock(flask_app, name: str) -> Optional[TextIO]:
    """Acquire a named non-blocking inter-process lock.

    The returned open handle owns the lock and must stay open until the work is
    finished. A thread-only fallback keeps development usable on platforms
    without ``fcntl``; production Linux/macOS uses the process-safe path.
    """
    handle = _lock_path(flask_app, name).open("a+", encoding="utf-8")
    if fcntl is None:
        return handle
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except (BlockingIOError, OSError):
        handle.close()
        return None


def _release_file_lock(handle: Optional[TextIO]) -> None:
    if handle is None:
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def _cleanup_stale_candidate_locks(flask_app) -> int:
    """Remove abandoned per-candidate lock files without touching live jobs."""
    if fcntl is None:
        return 0

    state_path = _state_path(flask_app)
    removed = 0
    pattern = f"{state_path.name}.candidate-*.lock"
    for path in state_path.parent.glob(pattern):
        try:
            handle = path.open("a+", encoding="utf-8")
        except OSError:
            logger.warning("Could not inspect discovery candidate lock %s", path, exc_info=True)
            continue

        acquired = False
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError):
                continue
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                logger.warning(
                    "Could not remove stale discovery candidate lock %s",
                    path,
                    exc_info=True,
                )
        finally:
            if acquired:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    logger.debug(
                        "Discovery candidate lock was already released: %s",
                        path,
                        exc_info=True,
                    )
            handle.close()

    if removed:
        logger.info("Removed %d stale discovery candidate lock file(s)", removed)
    return removed


@contextmanager
def _state_file_guard(flask_app) -> Iterator[None]:
    """Serialize read-modify-write state updates across threads and workers."""
    with _STATE_LOCK:
        handle = _lock_path(flask_app, "state").open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _read_state_unlocked(flask_app) -> dict[str, Any]:
    path = _state_path(flask_app)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
            raise ValueError("invalid discovery state shape")
    except FileNotFoundError:
        return _default_state()
    except (OSError, ValueError, json.JSONDecodeError):
        logger.warning("Discovery state could not be read; starting with an empty shortlist")
        return _default_state()

    state = _default_state()
    state.update(payload)
    state["count"] = len(state["candidates"])
    state["running"] = state.get("status") == "running"
    return state


def _read_state(flask_app) -> dict[str, Any]:
    with _state_file_guard(flask_app):
        return _read_state_unlocked(flask_app)


def _write_state_unlocked(flask_app, state: dict[str, Any]) -> None:
    path = _state_path(flask_app)
    state["count"] = len(state.get("candidates", []))
    state["running"] = state.get("status") == "running"
    state["updated_at"] = _utc_now()

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            json.dump(state, temporary, ensure_ascii=False, separators=(",", ":"))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _update_state(flask_app, updater) -> dict[str, Any]:
    with _state_file_guard(flask_app):
        state = _read_state_unlocked(flask_app)
        updater(state)
        _write_state_unlocked(flask_app, state)
        return state


def _candidate_id(candidate: dict[str, Any]) -> str:
    url = str(candidate.get("url") or "").strip()
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _public_candidate(candidate, rank: int) -> dict[str, Any]:
    payload = candidate.to_dict()
    payload.update(
        {
            "candidate_id": _candidate_id(payload),
            "rank": rank,
            "pipeline_status": "ready",
            "article_id": None,
            "failure_stage": None,
            "pipeline_error": None,
            "result": None,
        }
    )
    return payload


def _public_discovery_error(error: Exception) -> str:
    """Map known discovery failures to safe, actionable browser copy."""
    return _DISCOVERY_FAILURE_MESSAGES.get(
        str(getattr(error, "error_code", "") or ""),
        "Story discovery failed. Please try again.",
    )


def _run_discovery_worker(
    flask_app,
    owner: TextIO,
    trigger: str,
    run_version: int,
) -> None:
    try:
        # Local import avoids app.py <-> story_finder's existing app import cycle.
        from story_finder import discover_and_score

        ranked = discover_and_score()
        candidates = [
            _public_candidate(candidate, rank)
            for rank, candidate in enumerate(ranked, start=1)
        ]

        def complete(state):
            if int(state.get("run_version") or 0) != run_version:
                logger.info(
                    "Ignoring stale discovery result for run %d; current run is %d",
                    run_version,
                    int(state.get("run_version") or 0),
                )
                return
            state.update(
                {
                    "status": "complete",
                    "running": False,
                    "candidates": candidates,
                    "error": None,
                    "trigger": trigger,
                }
            )

        _update_state(flask_app, complete)
        logger.info("Discovery completed with %d ranked candidates (%s)", len(candidates), trigger)
    except Exception as error:
        logger.error("Discovery run failed", exc_info=True)
        public_error = _public_discovery_error(error)

        def fail(state):
            if int(state.get("run_version") or 0) != run_version:
                return
            state.update(
                {
                    "status": "failed",
                    "running": False,
                    "error": public_error,
                    "trigger": trigger,
                }
            )

        _update_state(flask_app, fail)
    finally:
        _release_file_lock(owner)


def start_discovery(flask_app, trigger: str = "manual") -> bool:
    """Start one discovery run, returning False when another worker owns it."""
    current_state = _read_state(flask_app)
    if any(
        candidate.get("pipeline_status") in {"queued", "processing"}
        for candidate in current_state.get("candidates", [])
    ):
        logger.info("Discovery deferred while a shortlist video is being created")
        return False

    owner = _try_file_lock(flask_app, "run")
    if owner is None:
        return False

    can_start = True

    def mark_running(state):
        nonlocal can_start
        if any(
            candidate.get("pipeline_status") in {"queued", "processing"}
            for candidate in state.get("candidates", [])
        ):
            can_start = False
            return
        state["run_version"] = int(state.get("run_version") or 0) + 1
        state.update(
            {
                "status": "running",
                "running": True,
                # Do not leave stale actions usable while a replacement
                # shortlist is being assembled.
                "candidates": [],
                "started_at": _utc_now(),
                "error": None,
                "trigger": trigger,
            }
        )

    try:
        _cleanup_stale_candidate_locks(flask_app)
        running_state = _update_state(flask_app, mark_running)
        if not can_start:
            _release_file_lock(owner)
            return False
        run_version = int(running_state.get("run_version") or 0)
        thread = Thread(
            target=_run_discovery_worker,
            args=(flask_app, owner, trigger, run_version),
            name="clipper-story-discovery",
            daemon=True,
        )
        thread.start()
        return True
    except Exception:
        _release_file_lock(owner)
        logger.error("Could not start discovery worker", exc_info=True)
        raise


def _find_candidate(state: dict[str, Any], candidate_id: str) -> Optional[dict[str, Any]]:
    return next(
        (
            candidate
            for candidate in state.get("candidates", [])
            if candidate.get("candidate_id") == candidate_id
        ),
        None,
    )


def _public_pipeline_failure(result: dict[str, Any]) -> dict[str, Any]:
    """Return stage-specific failure context without leaking upstream details."""
    stage = str(result.get("failure_stage") or "pipeline")
    code = str(result.get("failure_code") or "")
    message = (
        _SCRAPE_FAILURE_MESSAGES.get(code)
        if stage == "scrape"
        else None
    ) or _PIPELINE_FAILURE_MESSAGES.get(
        stage,
        "Video pipeline failed before it could finish.",
    )
    return {
        "status": "failed",
        "article_id": result.get("article_id"),
        "failure_stage": stage,
        "failure_code": code or None,
        "pipeline_error": message,
        # Backward-compatible alias for existing clients.
        "error": message,
    }


def _run_candidate_worker(
    flask_app,
    owner: TextIO,
    candidate_id: str,
    candidate_payload: dict[str, Any],
    run_version: int,
    color_intensity: str = "vivid",
) -> None:
    try:
        def mark_processing(state):
            if int(state.get("run_version") or 0) != run_version:
                return
            saved = _find_candidate(state, candidate_id)
            if saved is not None:
                saved["pipeline_status"] = "processing"

        _update_state(flask_app, mark_processing)

        from story_finder import StoryCandidate, _process_candidate

        candidate = StoryCandidate(
            title=str(candidate_payload.get("title") or ""),
            url=str(candidate_payload.get("url") or ""),
            source=str(candidate_payload.get("source") or ""),
            summary=str(candidate_payload.get("summary") or ""),
            published=str(candidate_payload.get("published") or ""),
            reddit_score=int(candidate_payload.get("reddit_score") or 0),
            viral_score=candidate_payload.get("viral_score"),
            score_reason=str(candidate_payload.get("score_reason") or ""),
            use_rss_fallback=candidate_payload.get("use_rss_fallback") is True,
        )
        result = _process_candidate(
            candidate,
            color_intensity=color_intensity,
        )
        final_status = str(result.get("status") or "failed")
        public_result = result
        if final_status == "failed":
            # _process_candidate logs provider/scrape detail with a traceback.
            # Rebuild from allow-listed stage/code values so raw external
            # response strings can never reach the browser payload.
            public_result = _public_pipeline_failure(result)

        def finish(state):
            if int(state.get("run_version") or 0) != run_version:
                logger.info(
                    "Ignoring stale candidate result %s from run %d",
                    candidate_id,
                    run_version,
                )
                return
            saved = _find_candidate(state, candidate_id)
            if saved is None:
                return
            saved["pipeline_status"] = final_status
            saved["article_id"] = result.get("article_id")
            saved["result"] = public_result
            saved["failure_stage"] = public_result.get("failure_stage")
            saved["pipeline_error"] = public_result.get("pipeline_error")

        _update_state(flask_app, finish)
        logger.info("Discovery candidate %s finished with status %s", candidate_id, final_status)
    except Exception:
        logger.error("Discovery candidate pipeline failed", exc_info=True)

        def fail(state):
            if int(state.get("run_version") or 0) != run_version:
                return
            saved = _find_candidate(state, candidate_id)
            if saved is None:
                return
            saved["pipeline_status"] = "failed"
            public_failure = _public_pipeline_failure(
                {"status": "failed", "failure_stage": "pipeline"}
            )
            saved["result"] = public_failure
            saved["failure_stage"] = public_failure["failure_stage"]
            saved["pipeline_error"] = public_failure["pipeline_error"]

        _update_state(flask_app, fail)
    finally:
        _release_file_lock(owner)


def start_candidate_pipeline(
    flask_app,
    candidate_id: str,
    color_intensity: str = "vivid",
) -> tuple[str, Optional[dict[str, Any]]]:
    """Queue one shortlist candidate and guard against duplicate video jobs."""
    state = _read_state(flask_app)
    candidate = _find_candidate(state, candidate_id)
    if candidate is None:
        return "missing", None
    if candidate.get("pipeline_status") in {"queued", "processing", "video_done"}:
        return "already_running", candidate
    run_version = int(state.get("run_version") or 0)

    owner = _try_file_lock(flask_app, f"candidate-{candidate_id}")
    if owner is None:
        return "already_running", candidate

    queued = False

    def mark_queued(latest_state):
        nonlocal queued
        if int(latest_state.get("run_version") or 0) != run_version:
            return
        saved = _find_candidate(latest_state, candidate_id)
        if saved is not None:
            saved["pipeline_status"] = "queued"
            saved["result"] = None
            saved["failure_stage"] = None
            saved["pipeline_error"] = None
            queued = True

    try:
        _update_state(flask_app, mark_queued)
        if not queued:
            _release_file_lock(owner)
            return "missing", None
        thread = Thread(
            target=_run_candidate_worker,
            args=(
                flask_app,
                owner,
                candidate_id,
                dict(candidate),
                run_version,
                color_intensity,
            ),
            name=f"clipper-discovery-video-{candidate_id}",
            daemon=True,
        )
        thread.start()
        return "started", candidate
    except Exception:
        _release_file_lock(owner)
        logger.error("Could not start discovery candidate worker", exc_info=True)
        raise


def _parse_hour() -> int:
    raw_hour = os.getenv("DISCOVERY_HOUR_UTC", "9")
    try:
        hour = int(raw_hour)
    except (TypeError, ValueError):
        logger.warning("Invalid DISCOVERY_HOUR_UTC=%r; using 9", raw_hour)
        return 9
    if not 0 <= hour <= 23:
        logger.warning("DISCOVERY_HOUR_UTC must be 0-23; using 9")
        return 9
    return hour


def _shutdown_scheduler() -> None:
    global _scheduler, _scheduler_owner, _scheduler_retry_after
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            logger.debug("Discovery scheduler was already stopped", exc_info=True)
        _scheduler = None
    _release_file_lock(_scheduler_owner)
    _scheduler_owner = None
    _scheduler_retry_after = 0.0


def ensure_discovery_scheduler(flask_app) -> bool:
    """Start exactly one daily APScheduler across reloader/workers.

    This is called lazily from ``before_request``. The Werkzeug reloader's
    parent never serves a request, and only one Gunicorn worker can own the
    inter-process scheduler lock.
    """
    global _scheduler, _scheduler_owner, _scheduler_retry_after
    if os.getenv("DISCOVERY_ENABLED", "false").lower() != "true":
        return False
    if _scheduler is not None:
        return True
    if time.monotonic() < _scheduler_retry_after:
        return False

    with _SCHEDULER_LOCK:
        if _scheduler is not None:
            return True
        if time.monotonic() < _scheduler_retry_after:
            return False

        owner = _try_file_lock(flask_app, "scheduler")
        if owner is None:
            logger.info("Discovery scheduler is owned by another app worker")
            _scheduler_retry_after = time.monotonic() + _SCHEDULER_RETRY_SECONDS
            return False

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            hour = _parse_hour()
            scheduler = BackgroundScheduler(timezone=timezone.utc, daemon=True)
            scheduler.add_job(
                lambda: start_discovery(flask_app, trigger="scheduled"),
                trigger=CronTrigger(hour=hour, minute=0, timezone=timezone.utc),
                id="clipper-daily-story-discovery",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
            scheduler.start()
            _scheduler = scheduler
            _scheduler_owner = owner
            _scheduler_retry_after = 0.0
            logger.info("Daily web discovery scheduled for %02d:00 UTC", hour)
            return True
        except Exception:
            _release_file_lock(owner)
            _scheduler_retry_after = time.monotonic() + _SCHEDULER_RETRY_SECONDS
            logger.error("Could not start discovery scheduler", exc_info=True)
            return False


@discovery_bp.before_app_request
def _start_scheduler_after_first_request() -> None:
    ensure_discovery_scheduler(current_app._get_current_object())


@discovery_bp.post("/run")
def run_discovery_route():
    flask_app = current_app._get_current_object()
    try:
        started = start_discovery(flask_app, trigger="manual")
    except Exception:
        return jsonify({"error": "Story discovery could not be started"}), 500

    return (
        jsonify(
            {
                "message": (
                    "Story discovery started"
                    if started
                    else "Story discovery is already running"
                ),
                "started": started,
                "running": True,
            }
        ),
        202,
    )


@discovery_bp.get("/candidates")
def discovery_candidates_route():
    return jsonify(_read_state(current_app._get_current_object()))


@discovery_bp.post("/candidates/<candidate_id>/make-video")
def make_discovery_video_route(candidate_id: str):
    if len(candidate_id) != 16 or any(char not in "0123456789abcdef" for char in candidate_id):
        return jsonify({"error": "Story candidate not found"}), 404

    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    elif not isinstance(payload, dict):
        return jsonify({"error": "JSON body must be an object"}), 400

    from video_generator import (
        DEFAULT_COLOR_INTENSITY,
        normalize_color_intensity,
    )

    raw_color_intensity = payload.get(
        "color_intensity",
        DEFAULT_COLOR_INTENSITY,
    )
    if (
        not isinstance(raw_color_intensity, str)
        or raw_color_intensity.strip().lower()
        not in {"natural", "vivid", "electric"}
    ):
        return jsonify({"error": "Unknown color intensity"}), 400
    color_intensity = normalize_color_intensity(raw_color_intensity)

    try:
        outcome, candidate = start_candidate_pipeline(
            current_app._get_current_object(),
            candidate_id,
            color_intensity,
        )
    except Exception:
        return jsonify({"error": "Video creation could not be started"}), 500

    if outcome == "missing":
        return jsonify({"error": "Story candidate not found"}), 404

    return (
        jsonify(
            {
                "message": (
                    "Video creation started"
                    if outcome == "started"
                    else "This story is already being processed"
                ),
                "started": outcome == "started",
                "candidate": candidate,
            }
        ),
        202,
    )


atexit.register(_shutdown_scheduler)

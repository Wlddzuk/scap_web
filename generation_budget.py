"""Server-side provider balance checks for Clipper's generation budget UI.

Only normalized billing fields leave this module. Provider credentials and raw
provider errors stay on the server, and failed lookups are represented by
``None`` rather than a misleading zero balance.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import logging
import os
from threading import Lock
import time

import requests

from video_generator import (
    BASE_VIDEO_ESTIMATED_COST_USD,
    MAX_VIDEO_CLIPS_PER_VIDEO,
    MAX_VIDEO_ESTIMATED_COST_USD,
    VIDEO_CLIP_ESTIMATED_COST_USD,
)


logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
REQUEST_TIMEOUT_SECONDS = 5
DEFAULT_BUDGET_LOW_USD = 5.0
DEFAULT_BUDGET_CRITICAL_USD = 1.0
FAL_USER_BALANCE_URL = "https://rest.alpha.fal.ai/billing/user_balance"
FAL_ADMIN_BILLING_URL = "https://api.fal.ai/v1/account/billing"
# Backwards-compatible names for callers/tests that imported the old constant.
FAL_BALANCE_URL = FAL_USER_BALANCE_URL
FAL_BILLING_URL = FAL_ADMIN_BILLING_URL
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"

_cache_lock = Lock()
_cached_payload: dict | None = None
_cache_deadline = 0.0


def _configured(name: str) -> bool:
    """Return whether an environment credential is usable, not a placeholder."""
    value = os.getenv(name, "").strip()
    return bool(value and not value.lower().startswith("your_"))


def _number(value, field_name: str) -> float:
    """Normalize a provider number while rejecting booleans/NaN/infinity."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is unavailable")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if not number.is_finite():
        raise ValueError(f"{field_name} is invalid")
    return float(number)


def _optional_number(value, field_name: str) -> float | None:
    return None if value is None else _number(value, field_name)


def _request_json(url: str, *, headers: dict[str, str], params=None):
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _budget_threshold(name: str, default: float) -> float:
    """Read a non-negative threshold, falling back safely when malformed."""
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = _number(raw_value, name)
    except ValueError:
        return default
    return value if value >= 0 else default


def _balance_severity(
    balance: float | None,
    *,
    low_usd: float,
    critical_usd: float,
) -> str:
    if balance is None:
        return "unavailable"
    if balance < critical_usd:
        return "critical"
    if balance < low_usd:
        return "low"
    return "ready"


def _fal_admin_key() -> str:
    """Return the optional billing key, including the legacy env name."""
    for name in ("FAL_ADMIN_BILLING_KEY", "FAL_ADMIN_KEY"):
        if _configured(name):
            return os.getenv(name, "").strip()
    return ""


def _fal_budget() -> dict:
    inference_configured = _configured("FAL_KEY")
    inference_key = os.getenv("FAL_KEY", "").strip() if inference_configured else ""
    admin_key = _fal_admin_key()
    result = {
        "configured": inference_configured,
        "admin_configured": bool(admin_key),
        "balance_lookup_configured": bool(admin_key or inference_key),
        "available": False,
        "status": (
            "not_configured" if not (admin_key or inference_key) else "unavailable"
        ),
        "balance_usd": None,
        "currency": None,
        "severity": "unavailable",
        "dashboard_url": "https://fal.ai/dashboard/billing",
    }
    if not (admin_key or inference_key):
        return result

    # A billing-scoped admin key remains an optional override. A normal FAL
    # inference key uses the user_balance endpoint, which returns a bare JSON
    # number rather than an object.
    attempts = []
    if admin_key:
        attempts.append(("admin", admin_key))
    if inference_key and inference_key != admin_key:
        attempts.append(("inference", inference_key))

    for key_kind, billing_key in attempts:
        try:
            if key_kind == "admin":
                payload = _request_json(
                    FAL_ADMIN_BILLING_URL,
                    headers={"Authorization": f"Key {billing_key}"},
                    params={"expand": "credits"},
                )
                if not isinstance(payload, dict):
                    raise ValueError("FAL admin billing response is invalid")
                credits = payload.get("credits")
                if not isinstance(credits, dict):
                    raise ValueError("FAL credits are unavailable")
                balance = _number(credits.get("current_balance"), "FAL balance")
                currency = str(credits.get("currency") or "USD").strip().upper()
            else:
                payload = _request_json(
                    FAL_USER_BALANCE_URL,
                    headers={"Authorization": f"Key {billing_key}"},
                )
                balance = _number(payload, "FAL balance")
                currency = "USD"
            result.update(
                available=True,
                status="available",
                balance_usd=balance,
                currency=currency,
            )
            return result
        except Exception:
            continue

    # Do not put credential fragments or raw provider response text in logs.
    logger.warning("FAL generation budget lookup is unavailable")
    return result


def _openrouter_credits(result: dict) -> None:
    api_configured = _configured("OPENROUTER_API_KEY")
    management_configured = _configured("OPENROUTER_MANAGEMENT_KEY")
    if not (api_configured or management_configured):
        return

    keys = []
    if management_configured:
        keys.append(os.getenv("OPENROUTER_MANAGEMENT_KEY", "").strip())
    if api_configured:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if api_key not in keys:
            keys.append(api_key)

    for credits_key in keys:
        try:
            payload = _request_json(
                OPENROUTER_CREDITS_URL,
                headers={"Authorization": f"Bearer {credits_key}"},
            )
            if not isinstance(payload, dict):
                raise ValueError("OpenRouter credits response is invalid")
            data = payload.get("data")
            if not isinstance(data, dict):
                raise ValueError("OpenRouter credits are unavailable")
            total_credits = _number(data.get("total_credits"), "OpenRouter credits")
            total_usage = _number(data.get("total_usage"), "OpenRouter usage")
            result.update(
                balance_available=True,
                balance_usd=float(
                    Decimal(str(total_credits)) - Decimal(str(total_usage))
                ),
                total_credits_usd=total_credits,
                total_usage_usd=total_usage,
            )
            return
        except Exception:
            continue

    logger.warning("OpenRouter account balance lookup is unavailable")


def _openrouter_key_usage(result: dict) -> None:
    if not _configured("OPENROUTER_API_KEY"):
        return
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    try:
        payload = _request_json(
            OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("OpenRouter key usage is unavailable")
        result.update(
            key_usage_available=True,
            key_usage_usd=_number(data.get("usage"), "OpenRouter key usage"),
            key_limit_usd=_optional_number(data.get("limit"), "OpenRouter key limit"),
            key_limit_remaining_usd=_optional_number(
                data.get("limit_remaining"),
                "OpenRouter key limit remaining",
            ),
            key_limit_reset=(
                str(data.get("limit_reset")).strip()
                if data.get("limit_reset") is not None
                else None
            ),
        )
    except Exception:
        logger.warning("OpenRouter API-key usage lookup is unavailable")


def _openrouter_budget() -> dict:
    credits_configured = bool(
        _configured("OPENROUTER_API_KEY")
        or _configured("OPENROUTER_MANAGEMENT_KEY")
    )
    result = {
        "configured": _configured("OPENROUTER_API_KEY"),
        "management_configured": _configured("OPENROUTER_MANAGEMENT_KEY"),
        "available": False,
        "status": "not_configured",
        "balance_available": False,
        "balance_status": "unavailable" if credits_configured else "not_configured",
        "balance_usd": None,
        "total_credits_usd": None,
        "total_usage_usd": None,
        "key_usage_available": False,
        "key_usage_status": (
            "unavailable"
            if _configured("OPENROUTER_API_KEY")
            else "not_configured"
        ),
        "key_usage_usd": None,
        "key_limit_usd": None,
        "key_limit_remaining_usd": None,
        "key_limit_reset": None,
        "currency": "USD",
        "severity": "unavailable",
        "dashboard_url": "https://openrouter.ai/activity",
    }
    _openrouter_credits(result)
    _openrouter_key_usage(result)
    if result["balance_available"]:
        result["balance_status"] = "available"
    if result["key_usage_available"]:
        result["key_usage_status"] = "available"
    result["available"] = bool(
        result["balance_available"] or result["key_usage_available"]
    )
    if result["available"]:
        result["status"] = "available"
    elif result["configured"] or result["management_configured"]:
        result["status"] = "unavailable"
    return result


def _console_only_provider(key_name: str, dashboard_url: str) -> dict:
    configured = _configured(key_name)
    return {
        "configured": configured,
        # These providers expose quota/billing only in their consoles. A key's
        # presence does not prove that quota remains, so availability is unknown.
        "available": None,
        "status": "configured" if configured else "not_configured",
        "balance_usd": None,
        "severity": "unavailable",
        "quota_status": (
            "check_provider_dashboard" if configured else "not_configured"
        ),
        "dashboard_url": dashboard_url,
    }


def _estimate_payload() -> dict:
    standard = float(BASE_VIDEO_ESTIMATED_COST_USD)
    maximum = min(
        float(MAX_VIDEO_ESTIMATED_COST_USD),
        standard
        + float(MAX_VIDEO_CLIPS_PER_VIDEO) * float(VIDEO_CLIP_ESTIMATED_COST_USD),
    )
    return {
        "currency": "USD",
        "standard_video_usd": standard,
        "max_motion_video_usd": maximum,
        "motion_clip_usd": float(VIDEO_CLIP_ESTIMATED_COST_USD),
        "max_motion_clips": int(MAX_VIDEO_CLIPS_PER_VIDEO),
        "hard_cap_usd": float(MAX_VIDEO_ESTIMATED_COST_USD),
        "kind": "estimate",
    }


def _build_payload() -> dict:
    fal = _fal_budget()
    openrouter = _openrouter_budget()
    providers = {
        "fal": fal,
        "openrouter": openrouter,
        "groq": _console_only_provider(
            "GROQ_API_KEY",
            "https://console.groq.com/usage",
        ),
        "gemini": _console_only_provider(
            "GEMINI_API_KEY",
            "https://aistudio.google.com/usage",
        ),
    }
    estimates = _estimate_payload()
    low_usd = _budget_threshold("BUDGET_LOW_USD", DEFAULT_BUDGET_LOW_USD)
    critical_usd = _budget_threshold(
        "BUDGET_CRITICAL_USD",
        DEFAULT_BUDGET_CRITICAL_USD,
    )
    fal_balance = fal["balance_usd"] if fal["available"] else None
    openrouter_balance = (
        openrouter["balance_usd"] if openrouter["balance_available"] else None
    )
    fal["severity"] = _balance_severity(
        fal_balance,
        low_usd=low_usd,
        critical_usd=critical_usd,
    )
    openrouter["severity"] = _balance_severity(
        openrouter_balance,
        low_usd=low_usd,
        critical_usd=critical_usd,
    )

    readable_balances = [
        balance
        for balance in (fal_balance, openrouter_balance)
        if balance is not None
    ]
    limiting_balance = min(readable_balances) if readable_balances else None
    severity = _balance_severity(
        limiting_balance,
        low_usd=low_usd,
        critical_usd=critical_usd,
    )
    if limiting_balance is None:
        status = "unavailable"
    elif limiting_balance >= estimates["standard_video_usd"]:
        status = "ready"
    else:
        status = "limited"
    return {
        "status": status,
        "severity": severity,
        "thresholds": {
            "low_usd": low_usd,
            "critical_usd": critical_usd,
        },
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "providers": providers,
        "estimates": estimates,
        "limiting_balance_usd": limiting_balance,
        "standard_video_affordable": (
            limiting_balance >= estimates["standard_video_usd"]
            if limiting_balance is not None
            else None
        ),
        "max_motion_video_affordable": (
            limiting_balance >= estimates["max_motion_video_usd"]
            if limiting_balance is not None
            else None
        ),
    }


def get_generation_budget(force: bool = False) -> dict:
    """Return a cached, frontend-safe view of provider generation budgets."""
    global _cached_payload, _cache_deadline

    with _cache_lock:
        now = time.monotonic()
        if not force and _cached_payload is not None and now < _cache_deadline:
            payload = deepcopy(_cached_payload)
            payload["cached"] = True
            return payload

        payload = _build_payload()
        _cached_payload = deepcopy(payload)
        _cache_deadline = time.monotonic() + CACHE_TTL_SECONDS
        payload["cached"] = False
        return payload


def clear_generation_budget_cache() -> None:
    """Clear the process-local cache (used by tests and configuration reloads)."""
    global _cached_payload, _cache_deadline
    with _cache_lock:
        _cached_payload = None
        _cache_deadline = 0.0

"""Bounded real-image fetchers for Clipper's mixed visual mode.

The module deliberately knows nothing about MoviePy or the video generator. A
resize callback is injected by the caller, which keeps this module reusable and
avoids a circular import with ``video_generator``.
"""

from __future__ import annotations

import base64
from io import BytesIO
import ipaddress
import json
import logging
import math
import os
import queue
import re
import socket
import threading
import time
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse

from PIL import Image
import requests


logger = logging.getLogger(__name__)

NASA_SEARCH_URL = "https://images-api.nasa.gov/search"
REQUEST_TIMEOUT_SECONDS = 12
DOWNLOAD_TIMEOUT_SECONDS = 20
REQUEST_ATTEMPTS = 2
MAX_REDIRECTS = 3
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
MIN_SOURCE_PIXELS = 500_000
MIN_SOURCE_SHORT_EDGE = 480
MAX_SOURCE_PIXELS = 50_000_000
NASA_SLOT_DEADLINE_SECONDS = 30
NASA_MAX_CANDIDATE_ITEMS = 3
NASA_MAX_ASSETS_PER_ITEM = 2
DNS_TIMEOUT_SECONDS = 5
REFERENT_SLOT_DEADLINE_SECONDS = 35
WIKIMEDIA_API_URL = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API_URL = "https://api.openverse.org/v1/images/"
SMITHSONIAN_API_URL = "https://api.si.edu/openaccess/api/v1.0/search"


@dataclass(frozen=True)
class ImageCandidate:
    image_url: str
    source_name: str
    source_url: str
    license: str
    author: str = ""
    subject_text: str = ""


@dataclass
class ReferentImage:
    image: Image.Image
    source_name: str
    source_url: str
    license: str
    author: str
    subject_verified: bool = True
    verification_method: str = "vision"

    def audit_record(self, *, lane: str = "photo") -> dict:
        return {
            "lane": lane,
            "provider": self.source_name,
            "source_url": self.source_url,
            "license": self.license,
            "author": self.author,
            "subject_verified": bool(self.subject_verified),
            "verification_method": self.verification_method,
        }


_VISION_VERIFIER_DISABLED = False
_VISION_VERIFIER_LOCK = threading.Lock()
_OPENROUTER_VERIFIER_DISABLED = False
_OPENROUTER_VERIFIER_LOCK = threading.Lock()
_SUBJECT_STOPWORDS = frozenset({
    "and", "the", "with", "from", "into", "over", "under", "through",
    "image", "photo", "photograph", "view", "scene", "landscape", "field",
    "night", "sky", "surface", "planet", "showing", "wide", "close",
})


def _remaining_seconds(deadline: float | None, fallback: float) -> float:
    """Return a positive bounded wait without extending an absolute deadline."""
    if deadline is None or not math.isfinite(deadline):
        return max(0.001, float(fallback))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("real-image deadline exceeded")
    return max(0.001, remaining)


def _resolve_host(
    hostname: str,
    port: int,
    *,
    deadline: float | None = None,
):
    """Resolve a host without allowing platform DNS to block the render thread.

    ``socket.getaddrinfo`` has no portable per-call timeout. A daemon worker lets
    the caller enforce the real-image operation's absolute deadline without
    making interpreter shutdown wait for a stuck resolver.
    """
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def resolve() -> None:
        try:
            result_queue.put((True, socket.getaddrinfo(hostname, port)))
        except Exception as exc:
            result_queue.put((False, exc))

    worker = threading.Thread(
        target=resolve,
        name="clipper-real-image-dns",
        daemon=True,
    )
    worker.start()
    try:
        succeeded, result = result_queue.get(
            timeout=_remaining_seconds(deadline, DNS_TIMEOUT_SECONDS)
        )
    except queue.Empty as exc:
        raise TimeoutError("real-image DNS resolution timed out") from exc
    if not succeeded:
        raise result
    return result


def _validate_public_url(
    url: str,
    *,
    deadline: float | None = None,
) -> str:
    """Reject non-web and private-network URLs before a server-side fetch."""
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("image URL must be public HTTP(S)")

    for info in _resolve_host(
        parsed.hostname,
        parsed.port or 443,
        deadline=deadline,
    ):
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("image URL resolves to a non-public address")
    return parsed.geturl()


def _close_response(response) -> None:
    close = getattr(response, "close", None)
    if close:
        try:
            close()
        except Exception:
            pass


def _requests_get_with_deadline(
    url: str,
    *,
    deadline: float | None,
    timeout: float,
    **kwargs,
):
    """Run the complete Requests GET without trusting DNS to honor its timeout.

    Requests/urllib3 applies its timeout after DNS resolution on some platforms.
    The daemon worker therefore owns the response until it hands it to the
    caller. If the caller's wait expires first, any late response is closed by
    the worker instead of being leaked or mistaken for a retry result.
    """
    wait_seconds = min(
        max(0.001, float(timeout)),
        _remaining_seconds(deadline, timeout),
    )
    result_queue: queue.Queue = queue.Queue(maxsize=1)
    ownership_lock = threading.Lock()
    ownership = {"abandoned": False}

    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault(
        "User-Agent",
        "ClipperScience/1.0 (open-access visual sourcing; contact via local operator)",
    )

    def request() -> None:
        try:
            response = requests.get(url, timeout=timeout, headers=headers, **kwargs)
        except Exception as exc:
            with ownership_lock:
                if not ownership["abandoned"]:
                    result_queue.put_nowait((False, exc))
            return

        with ownership_lock:
            if ownership["abandoned"]:
                _close_response(response)
                return
            result_queue.put_nowait((True, response))

    worker = threading.Thread(
        target=request,
        name="clipper-real-image-request",
        daemon=True,
    )
    worker.start()
    try:
        succeeded, result = result_queue.get(timeout=wait_seconds)
    except queue.Empty as exc:
        late_result = None
        with ownership_lock:
            ownership["abandoned"] = True
            try:
                late_result = result_queue.get_nowait()
            except queue.Empty:
                pass
        if late_result and late_result[0]:
            _close_response(late_result[1])
        raise TimeoutError("real-image request deadline exceeded") from exc
    if not succeeded:
        raise result
    return result


def _request_with_retry(
    url: str,
    *,
    params: dict | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    deadline: float | None = None,
):
    """Make one request plus one retry, validating every redirect before fetch."""
    current_url = _validate_public_url(url, deadline=deadline)
    current_params = params
    last_error = None
    for _redirect_count in range(MAX_REDIRECTS + 1):
        response = None
        for attempt in range(REQUEST_ATTEMPTS):
            try:
                request_timeout = min(
                    float(timeout),
                    _remaining_seconds(deadline, timeout),
                )
                response = _requests_get_with_deadline(
                    current_url,
                    deadline=deadline,
                    timeout=request_timeout,
                    params=current_params,
                    allow_redirects=False,
                    stream=True,
                )
                status_code = int(getattr(response, "status_code", 200))
                if 300 <= status_code < 400:
                    location = (response.headers or {}).get("Location")
                    if not location:
                        response.raise_for_status()
                    next_url = _validate_public_url(
                        urljoin(current_url, location),
                        deadline=deadline,
                    )
                    _close_response(response)
                    current_url = next_url
                    current_params = None
                    break
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                _close_response(response)
                if attempt + 1 < REQUEST_ATTEMPTS:
                    retry_delay = 0.2
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            continue
                        retry_delay = min(retry_delay, remaining)
                    time.sleep(retry_delay)
        else:
            raise last_error or RuntimeError("image request failed")
        # A redirect broke the inner retry loop; validate/fetch the next hop.
        continue
    raise ValueError("too many image redirects")


def _iter_stream_with_deadline(source, deadline: float | None):
    """Yield a blocking response iterator without waiting past ``deadline``."""
    if deadline is None:
        yield from source
        return

    stream_queue: queue.Queue = queue.Queue(maxsize=1)
    stopped = threading.Event()
    finished = object()

    def produce() -> None:
        try:
            for chunk in source:
                while not stopped.is_set():
                    try:
                        stream_queue.put((True, chunk), timeout=0.05)
                        break
                    except queue.Full:
                        continue
                if stopped.is_set():
                    return
            while not stopped.is_set():
                try:
                    stream_queue.put((True, finished), timeout=0.05)
                    return
                except queue.Full:
                    continue
        except Exception as exc:
            while not stopped.is_set():
                try:
                    stream_queue.put((False, exc), timeout=0.05)
                    return
                except queue.Full:
                    continue

    worker = threading.Thread(
        target=produce,
        name="clipper-real-image-stream",
        daemon=True,
    )
    worker.start()
    try:
        while True:
            try:
                succeeded, value = stream_queue.get(
                    timeout=_remaining_seconds(deadline, DOWNLOAD_TIMEOUT_SECONDS)
                )
            except queue.Empty as exc:
                raise TimeoutError("real-image body deadline exceeded") from exc
            if not succeeded:
                raise value
            if value is finished:
                return
            yield value
    finally:
        stopped.set()


def _read_limited_bytes(
    response,
    max_bytes: int,
    *,
    deadline: float | None = None,
) -> bytes:
    """Read a streamed response within both byte and absolute-time ceilings."""
    try:
        _remaining_seconds(deadline, DOWNLOAD_TIMEOUT_SECONDS)
        headers = getattr(response, "headers", None) or {}
        content_length = headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ValueError("response exceeds byte limit")
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid or oversized Content-Length") from exc

        chunks = []
        size = 0
        iterator = getattr(response, "iter_content", None)
        if iterator:
            source = iterator(chunk_size=64 * 1024)
        else:
            source = [getattr(response, "content", b"")]
        for chunk in _iter_stream_with_deadline(source, deadline):
            _remaining_seconds(deadline, DOWNLOAD_TIMEOUT_SECONDS)
            if not chunk:
                continue
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("response exceeds byte limit")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        close = getattr(response, "close", None)
        if close:
            close()


def _json_value_from_response(response, *, deadline: float | None = None):
    payload = _read_limited_bytes(
        response,
        MAX_JSON_BYTES,
        deadline=deadline,
    )
    return json.loads(payload.decode("utf-8"))


def _json_from_response(response, *, deadline: float | None = None) -> dict:
    parsed = _json_value_from_response(response, deadline=deadline)
    if not isinstance(parsed, dict):
        raise ValueError("JSON response is not an object")
    return parsed


def _image_from_response(
    response,
    *,
    resize_fn,
    target_width: int,
    target_height: int,
    deadline: float | None = None,
) -> Image.Image | None:
    payload = _read_limited_bytes(
        response,
        MAX_IMAGE_BYTES,
        deadline=deadline,
    )
    if not payload:
        return None

    image = Image.open(BytesIO(payload))
    width, height = image.size
    if (
        width * height > MAX_SOURCE_PIXELS
        or
        width * height < MIN_SOURCE_PIXELS
        or min(width, height) < MIN_SOURCE_SHORT_EDGE
    ):
        return None
    image.load()
    image = image.convert("RGB")
    return resize_fn(image, target_width, target_height)


def fetch_hero_image(
    url: str,
    *,
    resize_fn,
    target_width: int,
    target_height: int,
) -> Image.Image | None:
    """Fetch a sufficiently large article hero, or return None."""
    try:
        deadline = time.monotonic() + DOWNLOAD_TIMEOUT_SECONDS
        response = _request_with_retry(
            url,
            timeout=DOWNLOAD_TIMEOUT_SECONDS,
            deadline=deadline,
        )
        return _image_from_response(
            response,
            resize_fn=resize_fn,
            target_width=target_width,
            target_height=target_height,
            deadline=deadline,
        )
    except Exception as exc:
        logger.info("[RealImage] Hero unavailable; using AI fallback: %s", exc)
        return None


def _match_score(item: dict, query_words: set[str]) -> int:
    data = (item.get("data") or [{}])[0]
    fields = [
        str(data.get("title") or ""),
        str(data.get("description") or ""),
        " ".join(str(value) for value in (data.get("keywords") or [])),
    ]
    haystack = " ".join(fields).lower()
    return sum(3 if word in str(data.get("title") or "").lower() else 1 for word in query_words if word in haystack)


def _search_items(query: str, *, deadline: float) -> list[dict]:
    response = _request_with_retry(
        NASA_SEARCH_URL,
        params={
            "q": " ".join(str(query or "space").split()[:10]),
            "media_type": "image",
            "page_size": 24,
        },
        deadline=deadline,
    )
    payload = _json_from_response(response, deadline=deadline)
    items = payload.get("collection", {}).get("items", [])
    query_words = {
        word.lower()
        for word in str(query).replace("-", " ").split()
        if len(word) >= 3
    }
    return [
        item
        for _position, item in sorted(
            enumerate(items),
            key=lambda pair: (-_match_score(pair[1], query_words), pair[0]),
        )
    ]


def _is_nasa_asset_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "nasa.gov" or host.endswith(".nasa.gov")


def _manifest_urls(item: dict, *, deadline: float) -> list[str]:
    urls = []
    manifest_url = item.get("href")
    if manifest_url and _is_nasa_asset_url(manifest_url):
        try:
            response = _request_with_retry(
                manifest_url,
                deadline=deadline,
            )
            manifest = _json_value_from_response(
                response,
                deadline=deadline,
            )
            if isinstance(manifest, list):
                assets = manifest
            elif isinstance(manifest, dict):
                assets = manifest.get("collection", {}).get("items", [])
            else:
                assets = []
            for asset in assets:
                href = asset.get("href") if isinstance(asset, dict) else asset
                if href and _is_nasa_asset_url(href):
                    urls.append(href)
        except Exception:
            pass

    for link in item.get("links") or []:
        href = link.get("href") if isinstance(link, dict) else None
        if href and _is_nasa_asset_url(href):
            urls.append(href)

    def is_image_asset(url: str) -> bool:
        return url.lower().split("?", 1)[0].endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        )

    def quality(url: str):
        lowered = url.lower()
        original = any(term in lowered for term in ("~orig", "_orig", "original"))
        small = any(term in lowered for term in ("thumb", "small", "~medium"))
        return (original, not small)

    return sorted(
        (url for url in dict.fromkeys(urls) if is_image_asset(url)),
        key=quality,
        reverse=True,
    )


def fetch_nasa_image(
    query: str,
    *,
    resize_fn,
    target_width: int,
    target_height: int,
    preferred_index: int = 0,
) -> Image.Image | None:
    """Return a best-matching NASA image, with a silent per-slot fallback."""
    try:
        deadline = time.monotonic() + NASA_SLOT_DEADLINE_SECONDS
        items = _search_items(query, deadline=deadline)
        if not items:
            return None
        offset = max(0, int(preferred_index)) % len(items)
        ordered = items[offset:] + items[:offset]
        for item in ordered[:NASA_MAX_CANDIDATE_ITEMS]:
            if time.monotonic() >= deadline:
                return None
            for url in _manifest_urls(item, deadline=deadline)[
                :NASA_MAX_ASSETS_PER_ITEM
            ]:
                try:
                    response = _request_with_retry(
                        url,
                        timeout=DOWNLOAD_TIMEOUT_SECONDS,
                        deadline=deadline,
                    )
                    image = _image_from_response(
                        response,
                        resize_fn=resize_fn,
                        target_width=target_width,
                        target_height=target_height,
                        deadline=deadline,
                    )
                    if image is not None:
                        return image
                except Exception:
                    continue
    except Exception as exc:
        logger.info("[NASA] Image unavailable; using AI fallback: %s", exc)
    return None


def _plain_text(value) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def _is_public_domain(license_name: str) -> bool:
    normalized = str(license_name or "").casefold().replace("-", " ")
    return any(term in normalized for term in ("public domain", "cc0", "no known copyright"))


def candidate_has_required_attribution(candidate: ImageCandidate) -> bool:
    """Reject legally incomplete records before their bytes enter a render."""
    if not candidate.source_url or not candidate.license:
        return False
    license_key = candidate.license.casefold().replace("-", " ")
    if re.search(r"\b(?:nc|noncommercial|non commercial|nd|no derivatives)\b", license_key):
        return False
    return _is_public_domain(candidate.license) or bool(candidate.author.strip())


def candidate_is_still_visual(candidate: ImageCandidate) -> bool:
    """Exclude document thumbnails masquerading as usable scene imagery."""
    source_key = str(candidate.source_url or "").casefold().split("?", 1)[0]
    subject_key = str(candidate.subject_text or "")[:300].casefold()
    return not (
        source_key.endswith(".pdf")
        or ".pdf/" in source_key
        or re.search(
            r"\b(?:pdf|electronic resource|catalogue scan)\b",
            subject_key,
        )
    )


def _wikimedia_candidates(query: str, *, deadline: float) -> list[ImageCandidate]:
    response = _request_with_retry(
        WIKIMEDIA_API_URL,
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": " ".join(str(query).split()[:8]),
            "gsrnamespace": 6,
            "gsrlimit": 8,
            "prop": "imageinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1800,
            "format": "json",
            "formatversion": 2,
        },
        deadline=deadline,
    )
    payload = _json_from_response(response, deadline=deadline)
    candidates = []
    for page in payload.get("query", {}).get("pages", []) or []:
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata") or {}
        value = lambda key: _plain_text((metadata.get(key) or {}).get("value"))
        candidate = ImageCandidate(
            image_url=info.get("thumburl") or info.get("url") or "",
            source_name="Wikimedia Commons",
            source_url=info.get("descriptionurl") or "",
            license=value("LicenseShortName") or value("UsageTerms"),
            author=value("Artist") or value("Credit"),
            subject_text=" ".join((
                _plain_text(page.get("title")),
                value("ImageDescription"),
                value("ObjectName"),
                value("Categories"),
            )),
        )
        if candidate.image_url and candidate_has_required_attribution(candidate):
            candidates.append(candidate)
    return candidates


def _openverse_candidates(
    query: str,
    *,
    deadline: float,
    source_filter: str | None = None,
) -> list[ImageCandidate]:
    params = {
        "q": " ".join(str(query).split()[:8]),
        "page_size": 8,
        "mature": "false",
        "license_type": "commercial",
    }
    if source_filter:
        params["source"] = source_filter
    response = _request_with_retry(
        OPENVERSE_API_URL,
        params=params,
        deadline=deadline,
    )
    payload = _json_from_response(response, deadline=deadline)
    candidates = []
    for item in payload.get("results", []) or []:
        license_name = " ".join(
            part for part in (
                str(item.get("license") or "").upper(),
                str(item.get("license_version") or ""),
            ) if part
        ).strip()
        candidate = ImageCandidate(
            image_url=item.get("url") or item.get("thumbnail") or "",
            source_name=str(item.get("source") or "Openverse"),
            source_url=item.get("foreign_landing_url") or item.get("detail_url") or "",
            license=license_name,
            author=_plain_text(item.get("creator")),
            # Openverse tags are user-supplied and frequently describe nearby
            # subjects rather than what is visible. The title is the safer
            # fail-closed verifier when vision is quota-limited.
            subject_text=_plain_text(item.get("title")),
        )
        if candidate.image_url and candidate_has_required_attribution(candidate):
            candidates.append(candidate)
    return candidates


def _smithsonian_candidates(query: str, *, deadline: float) -> list[ImageCandidate]:
    api_key = os.getenv("SMITHSONIAN_API_KEY", "").strip()
    if not api_key:
        return []
    response = _request_with_retry(
        SMITHSONIAN_API_URL,
        params={"q": " ".join(str(query).split()[:8]), "rows": 8, "api_key": api_key},
        deadline=deadline,
    )
    payload = _json_from_response(response, deadline=deadline)
    candidates = []
    for row in payload.get("response", {}).get("rows", []) or []:
        content = row.get("content") or {}
        media = (
            content.get("descriptiveNonRepeating", {})
            .get("online_media", {})
            .get("media", [])
        )
        for item in media[:2]:
            image_url = item.get("content") or item.get("thumbnail") or ""
            if image_url:
                candidates.append(ImageCandidate(
                    image_url=image_url,
                    source_name="Smithsonian Open Access",
                    source_url=row.get("url") or "https://www.si.edu/openaccess",
                    license="CC0",
                    author="",
                    subject_text=" ".join((
                        _plain_text(row.get("title")),
                        _plain_text(row.get("type")),
                    )),
                ))
    return candidates


def _nasa_candidates(query: str, *, deadline: float) -> list[ImageCandidate]:
    candidates = []
    for item in _search_items(query, deadline=deadline)[:NASA_MAX_CANDIDATE_ITEMS]:
        data = (item.get("data") or [{}])[0]
        for image_url in _manifest_urls(item, deadline=deadline)[:1]:
            candidates.append(ImageCandidate(
                image_url=image_url,
                source_name="NASA Image Library",
                source_url=data.get("nasa_id") and f"https://images.nasa.gov/details/{data['nasa_id']}" or "https://images.nasa.gov/",
                license="Public domain",
                author=_plain_text(data.get("center")),
                subject_text=" ".join((
                    _plain_text(data.get("title")),
                    _plain_text(data.get("description")),
                    _plain_text(data.get("keywords")),
                )),
            ))
    return candidates


def _subject_tokens(value: str) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if len(token) >= 3 and token not in _SUBJECT_STOPWORDS
    }
    return tokens


def metadata_subject_matches(candidate: ImageCandidate, referent_query: str) -> bool:
    """Verify subject identity from provider-controlled title/description data."""
    query_tokens = _subject_tokens(referent_query)
    subject_tokens = _subject_tokens(candidate.subject_text)
    if not query_tokens or not subject_tokens:
        return False
    overlap = query_tokens & subject_tokens
    required = 1 if len(query_tokens) <= 2 else 2
    return len(overlap) >= required


def authoritative_metadata_can_override_vision(
    candidate: ImageCandidate,
    referent_query: str,
) -> bool:
    """Trust a strong museum/agency catalogue match over a weak vision model."""
    if candidate.source_name not in {"Wikimedia Commons", "Smithsonian", "NASA"}:
        return False
    source_key = candidate.source_url.casefold()
    subject_key = candidate.subject_text[:240].casefold()
    if ".pdf" in source_key or ".pdf" in subject_key:
        return False
    query_tokens = _subject_tokens(referent_query)
    overlap = query_tokens & _subject_tokens(candidate.subject_text)
    return len(query_tokens) >= 2 and len(overlap) >= 2


def _subject_verification_prompt(referent_query: str, subject: str = "") -> str:
    """Build the vision check, anchored on the story subject when one is known.

    Checking only ``referent_query`` verifies the search string rather than the
    story, so a wrong image scores as correct whenever the query itself has
    drifted: a photo of bundled plastic bags truthfully answers "Individual
    bundle" and was accepted for a scene about cells bundling together. Naming
    the story makes an off-topic image fail even when it matches the words.
    """
    if subject:
        return (
            "Answer only YES or NO. This image will illustrate a moment in a video "
            f"about: {subject}. The specific thing it should show is "
            f"'{referent_query}'. Would a viewer of that video see this image as "
            "showing that subject, rather than an unrelated object that merely "
            "matches the words? Answer NO if the main subject belongs to a "
            "different topic, or if there is a prominent text overlay, watermark, "
            "or signature. Supporting footage of the real object, event, "
            "researchers, or equipment counts as YES."
        )
    return (
        "Answer only YES or NO. Is this a truthful, relevant documentary image for "
        f"the named subject '{referent_query}', without a prominent text overlay, "
        "watermark, signature, or unrelated dominant subject? Supporting footage of "
        "the real object, event, researchers, or equipment counts as YES; it does not "
        "need to be a perfect product-style view."
    )


def _verify_subject_gemini(image: Image.Image, referent_query: str, subject: str = "") -> bool | None:
    """Return Gemini's verdict, or None when Gemini cannot verify the image."""
    global _VISION_VERIFIER_DISABLED

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    with _VISION_VERIFIER_LOCK:
        disabled = _VISION_VERIFIER_DISABLED
    if not api_key or disabled:
        return None
    try:
        import google.generativeai as genai

        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_VISION_MODEL", "gemini-3.1-flash-lite").strip()
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            [_subject_verification_prompt(referent_query, subject), image.copy()],
            request_options={"timeout": 15},
        )
        answer = str(getattr(response, "text", "") or "").strip().upper()
        if answer.startswith("YES"):
            return True
        if answer.startswith("NO"):
            return False
        return None
    except Exception as exc:
        message = str(exc)
        if "429" in message or "quota" in message.casefold() or "rate" in message.casefold():
            with _VISION_VERIFIER_LOCK:
                _VISION_VERIFIER_DISABLED = True
            logger.warning(
                "[RealImage] Gemini vision quota unavailable; switching to OpenRouter "
                "for the rest of this process"
            )
        else:
            logger.info("[RealImage] Vision verification unavailable: %s", exc)
        return None


def _openrouter_vision_models() -> list[str]:
    configured = os.getenv(
        "OPENROUTER_VISION_MODELS",
        "openai/gpt-4o-mini,openrouter/free",
    )
    return [model.strip() for model in configured.split(",") if model.strip()]


def _verify_subject_openrouter(image: Image.Image, referent_query: str, subject: str = "") -> bool | None:
    """Return an OpenRouter vision verdict, with provider-side model fallbacks."""
    global _OPENROUTER_VERIFIER_DISABLED

    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    models = _openrouter_vision_models()
    with _OPENROUTER_VERIFIER_LOCK:
        disabled = _OPENROUTER_VERIFIER_DISABLED
    if not api_key or not models or disabled:
        return None

    try:
        prepared = image.convert("RGB")
        prepared.thumbnail((768, 768), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        prepared.save(buffer, format="JPEG", quality=82, optimize=True)
        image_url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://clipper.local",
                "X-Title": "Clipper",
            },
            json={
                "models": models,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _subject_verification_prompt(referent_query, subject)},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }],
                "temperature": 0,
                "max_tokens": 5,
            },
            timeout=20,
        )
        if response.status_code >= 400:
            if response.status_code in {401, 402, 403, 429}:
                with _OPENROUTER_VERIFIER_LOCK:
                    _OPENROUTER_VERIFIER_DISABLED = True
                logger.warning(
                    "[RealImage] OpenRouter vision unavailable (HTTP %s); using strict "
                    "provider metadata for the rest of this process",
                    response.status_code,
                )
            else:
                logger.info("[RealImage] OpenRouter vision HTTP %s", response.status_code)
            return None
        payload = response.json()
        answer = str(
            (((payload.get("choices") or [{}])[0].get("message") or {}).get("content"))
            or ""
        ).strip().upper()
        if answer.startswith("YES"):
            return True
        if answer.startswith("NO"):
            return False
        return None
    except Exception as exc:
        logger.info("[RealImage] OpenRouter vision verification unavailable: %s", exc)
        return None


def _verify_subject(image: Image.Image, referent_query: str, subject: str = "") -> bool | None:
    """Verify with Gemini, then OpenRouter, or report provider unavailability."""
    gemini_result = _verify_subject_gemini(image, referent_query, subject)
    if gemini_result is not None:
        return gemini_result
    return _verify_subject_openrouter(image, referent_query, subject)


_SPACE_SUBJECT_TERMS = frozenset({
    "apollo", "asteroid", "astronaut", "astronomy", "atmosphere", "comet",
    "cosmic", "cosmos", "eclipse", "exoplanet", "galaxy", "gemini", "interstellar",
    "jupiter", "lunar", "mars", "mercury", "meteor", "moon", "nasa", "nebula",
    "neptune", "orbit", "orbital", "planet", "planetary", "pluto", "probe",
    "rocket", "satellite", "saturn", "solar", "space", "spacecraft", "spaceflight",
    "star", "stars", "stellar", "sun", "telescope", "universe", "uranus", "venus",
})


def _is_space_subject(text: str) -> bool:
    """Return whether a story is actually about space."""
    tokens = set(re.findall(r"[a-z0-9]+", str(text or "").casefold()))
    return bool(tokens & _SPACE_SUBJECT_TERMS)


def _call_verifier(verify, image: Image.Image, query: str, subject: str):
    """Invoke a verifier that may predate the ``subject`` argument."""
    try:
        return verify(image, query, subject)
    except TypeError:
        return verify(image, query)


def fetch_referent_image(
    query: str,
    *,
    resize_fn,
    target_width: int,
    target_height: int,
    verify_fn=None,
    subject: str = "",
) -> ReferentImage | None:
    """Fetch and verify a real referent from bounded open-access providers.

    The caller must turn ``None`` into a code-rendered graphic. It must never
    turn a failed photo search into generated art: obscurity is not permission
    to invent visual evidence.
    """
    query = " ".join(str(query or "").split()[:8]).strip()
    if not query:
        return None
    deadline = time.monotonic() + REFERENT_SLOT_DEADLINE_SECONDS
    verify = verify_fn or _verify_subject
    providers = [
        ("Wikimedia", _wikimedia_candidates),
        ("Smithsonian", _smithsonian_candidates),
        ("Openverse", lambda q, *, deadline: _openverse_candidates(q, deadline=deadline)),
        ("Internet Archive", lambda q, *, deadline: _openverse_candidates(f"archive {q}", deadline=deadline)),
    ]
    # NASA's archive keyword-matches extremely well and is therefore actively
    # dangerous off-topic: it holds spaceflight experiments on fertilization,
    # cells and cooperation, so a biology story scored perfect matches and got
    # Gemini spacecraft schematics. Only consult it for space subjects.
    if _is_space_subject(f"{subject} {query}"):
        providers.insert(2, ("NASA", _nasa_candidates))
        providers.append(
            ("ESA", lambda q, *, deadline: _openverse_candidates(f"ESA {q}", deadline=deadline))
        )
    providers = tuple(providers)
    for provider_name, search in providers:
        if time.monotonic() >= deadline:
            break
        try:
            candidates = search(query, deadline=deadline)
        except Exception as exc:
            logger.info("[RealImage] %s search unavailable: %s", provider_name, exc)
            continue
        for candidate in candidates[:3]:
            if time.monotonic() >= deadline:
                return None
            if not candidate_is_still_visual(candidate):
                logger.info(
                    "[RealImage] Rejected document thumbnail: %s",
                    candidate.source_url,
                )
                continue
            if not candidate_has_required_attribution(candidate):
                continue
            if not metadata_subject_matches(candidate, query):
                continue
            try:
                response = _request_with_retry(
                    candidate.image_url,
                    timeout=DOWNLOAD_TIMEOUT_SECONDS,
                    deadline=deadline,
                )
                image = _image_from_response(
                    response,
                    resize_fn=resize_fn,
                    target_width=target_width,
                    target_height=target_height,
                    deadline=deadline,
                )
                if image is None:
                    continue
                vision_result = _call_verifier(verify, image, query, subject)
                # A catalogue match may only rescue a vision rejection when the
                # story subject itself is in the metadata. Without that clause a
                # strong NASA match on "fertilizing sperm" overrode vision and
                # put a Gemini spacecraft schematic into a biology video.
                metadata_override = (
                    vision_result is False
                    and authoritative_metadata_can_override_vision(candidate, query)
                    and (
                        not subject
                        or metadata_subject_matches(candidate, subject)
                    )
                )
                if vision_result is False and not metadata_override:
                    logger.info(
                        "[RealImage] Vision rejected %s candidate for %r: %s",
                        candidate.source_name,
                        query,
                        candidate.source_url,
                    )
                    continue
                if vision_result is True:
                    method = "vision+metadata"
                elif metadata_override:
                    method = "authoritative provider metadata"
                    logger.info(
                        "[RealImage] Trusted authoritative catalogue match for %r: %s",
                        query,
                        candidate.source_url,
                    )
                else:
                    method = "provider metadata"
                return ReferentImage(
                    image=image,
                    source_name=candidate.source_name,
                    source_url=candidate.source_url,
                    license=candidate.license,
                    author=candidate.author,
                    subject_verified=True,
                    verification_method=method,
                )
            except Exception:
                continue
    return None

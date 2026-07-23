"""TikTok OAuth and Content Posting API client for Clipper.

This module deliberately contains no Flask or database code.  Keeping the HTTP
contract isolated makes it straightforward to test without touching a real
TikTok account.
"""

from __future__ import annotations

import base64
import hashlib
import math
from dataclasses import dataclass
from typing import Any, BinaryIO

import requests
from cryptography.fernet import Fernet, InvalidToken


AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
PUBLISH_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
PUBLISH_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"

VIDEO_METRIC_FIELDS = (
    "id",
    "create_time",
    "title",
    "video_description",
    "duration",
    "view_count",
    "like_count",
    "comment_count",
    "share_count",
)

MIN_CHUNK_SIZE = 5_000_000
MAX_CHUNK_SIZE = 64_000_000
MAX_FINAL_CHUNK_SIZE = 128_000_000
MAX_CHUNKS = 1_000
DEFAULT_MULTIPART_CHUNK_SIZE = 32_000_000
MAX_VIDEO_SIZE = 4_000_000_000


class TikTokAPIError(RuntimeError):
    """A normalized error returned by TikTok or its upload host."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "tiktok_error",
        log_id: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.log_id = log_id
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "log_id": self.log_id,
        }


def _request(request_call, *args, **kwargs) -> requests.Response:
    """Normalize transport failures without exposing request internals."""
    try:
        return request_call(*args, **kwargs)
    except requests.RequestException as exc:
        raise TikTokAPIError(
            "TikTok could not be reached",
            code="network_error",
        ) from exc


class TokenCipher:
    """Encrypt OAuth tokens before they are written to SQLite."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("A token encryption secret is required")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError(
                "Stored TikTok credentials could not be decrypted. "
                "Check TIKTOK_TOKEN_ENCRYPTION_KEY."
            ) from exc


def _json_response(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise TikTokAPIError(
            "TikTok returned a non-JSON response",
            code="invalid_response",
            status_code=response.status_code,
        ) from exc

    if not response.ok:
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or f"TikTok request failed ({response.status_code})"
            code = error.get("code") or "http_error"
            log_id = error.get("log_id")
        else:
            message = payload.get("error_description") or payload.get("message") or f"TikTok request failed ({response.status_code})"
            code = payload.get("error") or "http_error"
            log_id = payload.get("log_id")
        raise TikTokAPIError(message, code=str(code), log_id=log_id, status_code=response.status_code)

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("code") not in (None, "", "ok"):
        raise TikTokAPIError(
            error.get("message") or "TikTok rejected the request",
            code=error.get("code") or "tiktok_error",
            log_id=error.get("log_id"),
            status_code=response.status_code,
        )
    return payload


def exchange_code(
    *, client_key: str, client_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    response = _request(
        requests.post,
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    return _json_response(response)


def refresh_access_token(
    *, client_key: str, client_secret: str, refresh_token: str
) -> dict[str, Any]:
    response = _request(
        requests.post,
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    return _json_response(response)


def revoke_access_token(
    *, client_key: str, client_secret: str, access_token: str
) -> None:
    response = _request(
        requests.post,
        REVOKE_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "token": access_token,
        },
        timeout=30,
    )
    _json_response(response)


def _authorized_post(url: str, access_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = _request(
        requests.post,
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=payload,
        timeout=30,
    )
    return _json_response(response)


def query_creator_info(access_token: str) -> dict[str, Any]:
    payload = _authorized_post(CREATOR_INFO_URL, access_token, {})
    return payload.get("data") or {}


@dataclass(frozen=True)
class UploadPlan:
    video_size: int
    chunk_size: int
    total_chunk_count: int


def make_upload_plan(video_size: int) -> UploadPlan:
    """Build a TikTok-compliant sequential chunk plan.

    TikTok defines total_chunk_count as floor(video_size / chunk_size) and asks
    clients to merge trailing bytes into the final chunk. Videos up to 64 MB
    are therefore sent as a single request.
    """
    if video_size <= 0:
        raise ValueError("Video file is empty")
    if video_size > MAX_VIDEO_SIZE:
        raise ValueError("TikTok videos must be 4 GB or smaller")

    if video_size <= MAX_CHUNK_SIZE:
        return UploadPlan(video_size, video_size, 1)

    # A file over 64 MB must use more than one request. 32 MB keeps every
    # regular chunk inside TikTok's 5-64 MB band and the merged final chunk
    # below the 128 MB exception.
    chunk_size = DEFAULT_MULTIPART_CHUNK_SIZE
    total_chunks = math.floor(video_size / chunk_size)
    if total_chunks < 1 or total_chunks > MAX_CHUNKS:
        raise ValueError("Video requires an unsupported number of upload chunks")

    final_size = video_size - (chunk_size * (total_chunks - 1))
    if final_size > MAX_FINAL_CHUNK_SIZE:
        raise ValueError("Final TikTok upload chunk exceeds 128 MB")
    return UploadPlan(video_size, chunk_size, total_chunks)


def initialize_video_post(
    *,
    access_token: str,
    title: str,
    privacy_level: str,
    disable_comment: bool,
    disable_duet: bool,
    disable_stitch: bool,
    brand_content_toggle: bool,
    brand_organic_toggle: bool,
    upload_plan: UploadPlan,
) -> dict[str, Any]:
    payload = {
        "post_info": {
            "title": title,
            "privacy_level": privacy_level,
            "disable_duet": disable_duet,
            "disable_comment": disable_comment,
            "disable_stitch": disable_stitch,
            "video_cover_timestamp_ms": 1_000,
            "brand_content_toggle": brand_content_toggle,
            "brand_organic_toggle": brand_organic_toggle,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": upload_plan.video_size,
            "chunk_size": upload_plan.chunk_size,
            "total_chunk_count": upload_plan.total_chunk_count,
        },
    }
    response = _authorized_post(PUBLISH_INIT_URL, access_token, payload)
    data = response.get("data") or {}
    if not data.get("publish_id") or not data.get("upload_url"):
        raise TikTokAPIError("TikTok did not return an upload URL", code="missing_upload_url")
    return data


def _read_chunk(file_handle: BinaryIO, size: int) -> bytes:
    data = file_handle.read(size)
    if len(data) != size:
        raise TikTokAPIError("Could not read the complete video upload chunk", code="file_read_error")
    return data


def upload_video_file(upload_url: str, video_path: str, upload_plan: UploadPlan) -> None:
    """Upload the video sequentially to TikTok's short-lived upload URL."""
    total_size = upload_plan.video_size
    with open(video_path, "rb") as video_file:
        for index in range(upload_plan.total_chunk_count):
            start = index * upload_plan.chunk_size
            if index == upload_plan.total_chunk_count - 1:
                chunk_length = total_size - start
            else:
                chunk_length = upload_plan.chunk_size
            end = start + chunk_length - 1
            chunk = _read_chunk(video_file, chunk_length)

            response = _request(
                requests.put,
                upload_url,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(chunk_length),
                    "Content-Range": f"bytes {start}-{end}/{total_size}",
                },
                data=chunk,
                timeout=120,
            )
            if not response.ok:
                message = f"TikTok video upload failed ({response.status_code})"
                try:
                    payload = response.json()
                    message = payload.get("message") or payload.get("error", {}).get("message") or message
                except (ValueError, AttributeError):
                    pass
                raise TikTokAPIError(
                    message,
                    code="upload_failed",
                    status_code=response.status_code,
                )


def fetch_publish_status(access_token: str, publish_id: str) -> dict[str, Any]:
    payload = _authorized_post(
        PUBLISH_STATUS_URL,
        access_token,
        {"publish_id": publish_id},
    )
    return payload.get("data") or {}


def list_user_videos(
    access_token: str,
    *,
    max_count: int = 20,
    cursor: int | None = None,
) -> dict[str, Any]:
    """Return a creator's recent videos and public engagement counters.

    The Display API caps each page at 20 videos. Watch time is intentionally
    not requested because TikTok's public ``video.list`` contract does not
    expose it.
    """
    if not 1 <= max_count <= 20:
        raise ValueError("TikTok video list max_count must be between 1 and 20")

    body: dict[str, Any] = {"max_count": max_count}
    if cursor is not None:
        body["cursor"] = cursor
    response = _request(
        requests.post,
        VIDEO_LIST_URL,
        params={"fields": ",".join(VIDEO_METRIC_FIELDS)},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json=body,
        timeout=30,
    )
    return _json_response(response).get("data") or {}


def query_user_stats(access_token: str) -> dict[str, Any]:
    """Return account-level totals granted by ``user.info.stats``."""
    response = _request(
        requests.get,
        USER_INFO_URL,
        params={
            "fields": "open_id,display_name,follower_count,following_count,likes_count,video_count"
        },
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    payload = _json_response(response)
    return (payload.get("data") or {}).get("user") or {}

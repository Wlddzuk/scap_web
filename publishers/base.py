"""Small common contract and HTTP safeguards for publishing adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
import time
from typing import Any, Callable

import requests


DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 60
DEFAULT_TIMEOUT = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT)
DEFAULT_RETRY_BUDGET = 2
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class PublisherError(RuntimeError):
    """Normalized provider failure with a deliberately safe public message."""

    def __init__(
        self,
        public_message: str,
        *,
        code: str = 'publisher_error',
        status_code: int | None = None,
    ) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PublishResult:
    platform: str
    status: str
    accepted: bool
    external_id: str | None = None
    permalink: str | None = None
    error: str | None = None
    published_at: str | None = None
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Publisher(ABC):
    """The platform-neutral operation used by the fan-out coordinator."""

    platform: str

    @abstractmethod
    def publish(self, article: Any, video_path: str, options: dict[str, Any]) -> PublishResult:
        raise NotImplementedError

    @abstractmethod
    def check_status(self, external_id: str) -> PublishResult:
        raise NotImplementedError


def request_with_retries(
    request_call: Callable[..., requests.Response],
    *args: Any,
    retries: int = DEFAULT_RETRY_BUDGET,
    timeout: tuple[int, int] | int = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> requests.Response:
    """Make a bounded HTTP call and retry only transient transport failures.

    Provider response bodies are intentionally not included in raised public
    errors. Callers should log full exceptions at their application boundary.
    """
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            response = request_call(*args, timeout=timeout, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= retries:
                return response
            last_error = PublisherError(
                'The publishing service is temporarily unavailable',
                code='temporary_provider_error',
                status_code=response.status_code,
            )
        if attempt < retries:
            time.sleep(0.5 * (2 ** attempt))

    raise PublisherError(
        'The publishing service could not be reached',
        code='network_error',
    ) from last_error


def response_json(response: requests.Response, *, platform: str) -> dict[str, Any]:
    """Parse one provider response without leaking its body to the caller."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise PublisherError(
            f'{platform} returned an invalid response',
            code='invalid_response',
            status_code=response.status_code,
        ) from exc
    if not response.ok:
        raise PublisherError(
            f'{platform} could not complete the request',
            code='provider_rejected',
            status_code=response.status_code,
        )
    if not isinstance(payload, dict):
        raise PublisherError(
            f'{platform} returned an invalid response',
            code='invalid_response',
            status_code=response.status_code,
        )
    return payload

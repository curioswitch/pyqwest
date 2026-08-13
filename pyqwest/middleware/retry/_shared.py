from __future__ import annotations

from email.utils import parsedate_to_datetime
from enum import Enum
from http import HTTPStatus


class RetryMode(Enum):
    """Controls how request content is retained for retries."""

    BUFFERED = "buffered"
    """Streaming requests are fully buffered in memory for retries on connection errors and server responses."""

    UNBUFFERED = "unbuffered"
    """Streaming requests are not buffered in memory and can only be retried on connection errors."""


_IDEMPOTENT_METHODS = ("GET", "HEAD", "PUT", "DELETE")


def parse_retry_after(header: str | None) -> float | None:
    if header is None:
        return None
    # of seconds, e.g., Retry-After: 120
    try:
        ret = int(header)
        if ret < 0:
            return None
        return float(ret)
    except ValueError:
        pass

    # Date, e.g., Retry-After: Wed, 21 Oct 2015 07:28:00 GMT
    try:
        dt = parsedate_to_datetime(header)
    except Exception:
        return None

    delta = (dt - dt.now(dt.tzinfo)).total_seconds()
    if delta < 0:
        return None
    return delta


def default_should_retry_request(method: str) -> RetryMode:
    if method in _IDEMPOTENT_METHODS:
        return RetryMode.BUFFERED
    return RetryMode.UNBUFFERED


def normalize_retry_mode(*, value: bool | RetryMode) -> RetryMode | None:
    if isinstance(value, RetryMode):
        return value
    return RetryMode.BUFFERED if value else None


def default_should_retry_response(method: str, status: int | Exception) -> bool:
    if isinstance(status, ConnectionError):
        return True
    if method not in _IDEMPOTENT_METHODS:
        return False
    if isinstance(status, Exception):
        return True
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return True
    return status >= 500 and status != HTTPStatus.NOT_IMPLEMENTED

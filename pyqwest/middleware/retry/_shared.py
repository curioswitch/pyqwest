from __future__ import annotations

from email.utils import parsedate_to_datetime
from http import HTTPStatus


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


def default_should_retry_request(_method: str) -> bool:
    # By default, allow retries for any methods for connection errors.
    # The default response hook checks idempotency for other errors.
    return True


_IDEMPOTENT_METHODS = ("GET", "HEAD", "PUT", "DELETE")


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

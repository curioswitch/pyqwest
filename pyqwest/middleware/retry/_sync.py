from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, cast, final

from pyqwest import HTTPHeaderName, Multipart, Part, ReadError, SyncTransport
from pyqwest._pyqwest import SyncRequest, SyncResponse, _Backoff

from ._shared import (
    default_should_retry_request,
    default_should_retry_response,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


class SyncRetryTransport(SyncTransport):
    """Retry middleware for sync clients.

    Wrap a SyncTransport with this class to allow requests to be automatically retried.
    By default, known-safe errors are retried, meaning connection errors for any request,
    and I/O errors or 429/5xx responses for idempotent methods.

    The default behavior can be overridden by subclassing this class and overriding the
    `should_retry_request` and `should_retry_response` methods to suit any need.

    Examples:
        ```python
        from pyqwest import SyncClient, SyncHTTPTransport, SyncRequest
        from pyqwest.middleware.retry import SyncRetryTransport


        class MyRetryTransport(SyncRetryTransport):
            def should_retry_request(self, request: SyncRequest) -> bool:
                return not request.url.endswith("/unsafe-method")


        client = SyncClient(transport=MyRetryTransport(SyncHTTPTransport()))
        client.get("http://localhost/safe-method")  # will retry on transient errors
        client.get("http://localhost/unsafe-method")  # will not retry
        ```
    """

    _transport: SyncTransport
    _initial_interval: float
    _randomization_factor: float
    _multiplier: float
    _max_interval: float
    _max_retries: int

    def __init__(
        self,
        transport: SyncTransport,
        initial_interval: float = 0.5,
        randomization_factor: float = 0.5,
        multiplier: float = 1.5,
        max_interval: float = 60.0,
        max_retries: int = 4,
    ) -> None:
        self._transport = transport
        self._initial_interval = initial_interval
        self._randomization_factor = randomization_factor
        self._multiplier = multiplier
        self._max_interval = max_interval
        self._max_retries = max_retries

    @final
    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        if not self.should_retry_request(request):
            return self._transport.execute_sync(request)

        backoff = _Backoff(
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

        get_content: Callable[[], bytes | Iterator[bytes] | Multipart]

        content = request.content
        if isinstance(content, bytes):

            def _get_content() -> bytes:
                return content

            get_content = _get_content
        elif isinstance(content, Multipart):
            get_content = _retrying_multipart_content(content)
        else:
            retrying_content = RetryingRequestContent(content)
            get_content = retrying_content.get

        resp: SyncResponse | Exception

        try:
            resp = self._transport.execute_sync(
                SyncRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    content=get_content(),
                )
            )
        except Exception as e:
            resp = e

        retries = 0
        while True:
            if not self.should_retry_response(request, resp):
                break
            if isinstance(resp, SyncResponse):
                resp.close()
            retries += 1
            if retries > self._max_retries:
                if isinstance(resp, ConnectionError):
                    # Connection errors that don't resolve with retries are better
                    # surfaced as-is since they are network issues rather than backend.
                    raise resp
                msg = f"Maximum retry attempts exceeded: {self._max_retries}"
                if isinstance(resp, Exception):
                    raise ReadError(msg) from resp
                raise ReadError(msg)

            if (
                isinstance(resp, SyncResponse)
                and resp.status == HTTPStatus.TOO_MANY_REQUESTS
                and (
                    wt := parse_retry_after(
                        resp.headers.get(HTTPHeaderName.RETRY_AFTER)
                    )
                )
                is not None
            ):
                wait_time = wt
            else:
                wait_time = backoff.next_backoff()
            if wait_time is None:
                break

            time.sleep(wait_time)

            try:
                resp = self._transport.execute_sync(
                    SyncRequest(
                        method=request.method,
                        url=request.url,
                        headers=request.headers,
                        content=get_content(),
                    )
                )
            except Exception as e:
                resp = e

        if isinstance(resp, Exception):
            raise resp
        return resp

    def should_retry_request(self, request: SyncRequest) -> bool:
        return default_should_retry_request(request.method)

    def should_retry_response(
        self, request: SyncRequest, response: SyncResponse | Exception
    ) -> bool:
        return default_should_retry_response(
            request.method,
            response.status if isinstance(response, SyncResponse) else response,
        )


class RetryingRequestContent:
    def __init__(self, content: Iterator[bytes]) -> None:
        self._content = content
        self._buffer = bytearray()

    def get(self) -> Iterator[bytes]:
        if self._buffer:
            yield bytes(self._buffer)
        for chunk in self._content:
            self._buffer.extend(chunk)
            yield chunk


def _retrying_multipart_content(content: Multipart) -> Callable[[], Multipart]:
    parts = content.parts
    if all(isinstance(part.content, bytes) for _, part in parts):
        # Parts with buffered content can be replayed as is.
        return lambda: content

    # Stream parts are buffered as they are sent so retries can replay them.
    retrying_parts = [
        (
            name,
            part,
            None
            if isinstance(part.content, bytes)
            else RetryingRequestContent(cast("Iterator[bytes]", part.content)),
        )
        for name, part in parts
    ]

    def get() -> Multipart:
        return Multipart(
            [
                (
                    name,
                    part
                    if retrying is None
                    else Part(
                        retrying.get(),
                        filename=part.filename,
                        content_type=part.content_type,
                    ),
                )
                for name, part, retrying in retrying_parts
            ]
        )

    return get

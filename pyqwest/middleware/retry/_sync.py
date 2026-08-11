from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, SyncTransport
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

    A request body that is not `bytes` is buffered in memory as it is sent so it can be
    replayed on a retry. Pass `max_buffered_body_size` to bound that buffer: once a body
    exceeds the limit, buffering stops and the request is no longer replayed, so an error
    that would have been retried is surfaced as-is. By default the buffer is unbounded.

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
    _max_buffered_body_size: int | None

    def __init__(
        self,
        transport: SyncTransport,
        initial_interval: float = 0.5,
        randomization_factor: float = 0.5,
        multiplier: float = 1.5,
        max_interval: float = 60.0,
        max_retries: int = 4,
        max_buffered_body_size: int | None = None,
    ) -> None:
        self._transport = transport
        self._initial_interval = initial_interval
        self._randomization_factor = randomization_factor
        self._multiplier = multiplier
        self._max_interval = max_interval
        self._max_retries = max_retries
        self._max_buffered_body_size = max_buffered_body_size

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

        get_content: Callable[[], bytes | Iterator[bytes]]

        content = request.content
        retrying_content: RetryingRequestContent | None = None
        if isinstance(content, bytes):

            def _get_content() -> bytes:
                return content

            get_content = _get_content
        else:
            retrying_content = RetryingRequestContent(
                content, self._max_buffered_body_size
            )
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
            if retrying_content is not None and not retrying_content.replayable:
                # The body outgrew max_buffered_body_size, so it cannot be sent again.
                # Surface the original response or error instead of retrying.
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
    def __init__(
        self, content: Iterator[bytes], max_buffer_size: int | None = None
    ) -> None:
        self._content = content
        self._max_buffer_size = max_buffer_size
        self._buffer = bytearray()
        self._replayable = True

    @property
    def replayable(self) -> bool:
        """Whether everything sent so far is buffered and can be sent again."""
        return self._replayable

    def get(self) -> Iterator[bytes]:
        if self._buffer:
            yield bytes(self._buffer)
        for chunk in self._content:
            if self._replayable:
                if (
                    self._max_buffer_size is not None
                    and len(self._buffer) + len(chunk) > self._max_buffer_size
                ):
                    # Past the limit, stop recording and release what was recorded;
                    # the body can no longer be replayed.
                    self._replayable = False
                    self._buffer = bytearray()
                else:
                    self._buffer.extend(chunk)
            yield chunk

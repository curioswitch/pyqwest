from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, Transport
from pyqwest._pyqwest import Request, Response, _Backoff

from ._shared import (
    RetryMode,
    default_should_retry_request,
    default_should_retry_response,
    normalize_retry_mode,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class RetryTransport(Transport):
    """Retry middleware for async clients.

    Wrap a Transport with this class to allow requests to be automatically retried.
    By default, connection errors are retried for any request, while I/O errors and
    transient 429/5xx responses are retried only for GET, HEAD, PUT, and DELETE.

    The default behavior can be overridden by subclassing this class and overriding the
    `should_retry_request` and `should_retry_response` methods to suit any need.

    Examples:
        ```python
        from pyqwest import Client, HTTPTransport, Request
        from pyqwest.middleware.retry import RetryMode, RetryTransport


        class MyRetryTransport(RetryTransport):
            def should_retry_request(self, request: Request) -> bool | RetryMode:
                if request.url.endswith("/unsafe-method"):
                    return False
                return RetryMode.UNBUFFERED


        client = Client(transport=MyRetryTransport(HTTPTransport()))
        await client.get(
            "http://localhost/safe-method"
        )  # will retry on transient errors
        await client.get("http://localhost/unsafe-method")  # will not retry
        ```
    """

    _transport: Transport
    _initial_interval: float
    _randomization_factor: float
    _multiplier: float
    _max_interval: float
    _max_retries: int

    def __init__(
        self,
        transport: Transport,
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
    async def execute(self, request: Request) -> Response:
        retry_mode = normalize_retry_mode(value=self.should_retry_request(request))
        if retry_mode is None:
            return await self._transport.execute(request)

        backoff = _Backoff(
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

        get_content: Callable[[], bytes | AsyncIterator[bytes]]

        content = request.content
        content_started = False
        unbuffered_stream = (
            not isinstance(content, bytes) and retry_mode == RetryMode.UNBUFFERED
        )

        async def _close_content() -> None:
            aclose = getattr(content, "aclose", None)
            if aclose is not None:
                await aclose()

        if isinstance(content, bytes):

            def _get_content() -> bytes:
                return content

            get_content = _get_content
        elif unbuffered_stream:

            async def _unbuffered_content() -> AsyncIterator[bytes]:
                nonlocal content_started
                content_started = True
                try:
                    async for chunk in content:
                        yield chunk
                finally:
                    await _close_content()

            get_content = _unbuffered_content
        else:
            retrying_content = RetryingRequestContent(content)
            get_content = retrying_content.get

        resp: Response | Exception

        retries = 0
        # Retry connection errors regardless of retry mode.
        try:
            while True:
                try:
                    resp = await self._transport.execute(
                        Request(
                            method=request.method,
                            url=request.url,
                            headers=request.headers,
                            content=get_content(),
                        )
                    )
                except Exception as e:  # noqa: PERF203
                    if not self.should_retry_response(request, e):
                        raise
                    if unbuffered_stream and content_started:
                        # I/O happened for an unbuffered stream, can't retry.
                        raise
                    resp = e
                    retries += 1
                    self._check_retries(retries, e)
                    wait_time = backoff.next_backoff()
                    if wait_time is None:
                        raise
                    await asyncio.sleep(wait_time)
                else:
                    break
        except BaseException:
            if unbuffered_stream and not content_started:
                await _close_content()
            raise

        # Don't retry responses with a streaming request when we can't buffer.
        if unbuffered_stream:
            if not content_started:
                await _close_content()
            if isinstance(resp, Exception):
                raise resp
            return resp

        while True:
            if not self.should_retry_response(request, resp):
                break
            if isinstance(resp, Response):
                await resp.aclose()
            retries += 1
            self._check_retries(retries, resp)

            if (
                isinstance(resp, Response)
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

            await asyncio.sleep(wait_time)

            try:
                resp = await self._transport.execute(
                    Request(
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

    def should_retry_request(self, request: Request) -> bool | RetryMode:
        return default_should_retry_request(request.method)

    def should_retry_response(
        self, request: Request, response: Response | Exception
    ) -> bool:
        return default_should_retry_response(
            request.method,
            response.status if isinstance(response, Response) else response,
        )

    def _check_retries(self, retries: int, resp: Response | Exception) -> None:
        if retries > self._max_retries:
            if isinstance(resp, ConnectionError):
                # Connection errors that don't resolve with retries are better
                # surfaced as-is since they are network issues rather than backend.
                raise resp
            msg = f"Maximum retry attempts exceeded: {self._max_retries}"
            if isinstance(resp, Exception):
                raise ReadError(msg) from resp
            raise ReadError(msg)


class RetryingRequestContent:
    def __init__(self, content: AsyncIterator[bytes]) -> None:
        self._content = content
        self._buffer = bytearray()

    async def get(self) -> AsyncIterator[bytes]:
        if self._buffer:
            yield bytes(self._buffer)
        async for chunk in self._content:
            self._buffer.extend(chunk)
            yield chunk

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, Transport
from pyqwest._pyqwest import Request, Response, _Backoff

from ._shared import (
    default_should_retry_request,
    default_should_retry_response,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class RetryTransport(Transport):
    """Retry middleware for async clients.

    Wrap a Transport with this class to allow requests to be automatically retried.
    By default, known-safe errors are retried, meaning connection errors for any request,
    and I/O errors or 429/5xx responses for idempotent methods.

    The default behavior can be overridden by subclassing this class and overriding the
    `should_retry_request` and `should_retry_response` methods to suit any need.

    Examples:
        ```python
        from pyqwest import Client, HTTPTransport, Request
        from pyqwest.middleware.retry import RetryTransport


        class MyRetryTransport(RetryTransport):
            def should_retry_request(self, request: Request) -> bool:
                return not request.url.endswith("/unsafe-method")


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
        if not self.should_retry_request(request):
            return await self._transport.execute(request)

        backoff = _Backoff(
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

        get_content: Callable[[], bytes | AsyncIterator[bytes]]

        content = request.content
        if isinstance(content, bytes):

            def _get_content() -> bytes:
                return content

            get_content = _get_content
        else:
            retrying_content = RetryingRequestContent(content)
            get_content = retrying_content.get

        resp: Response | Exception

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

        retries = 0
        while True:
            if not self.should_retry_response(request, resp):
                break
            if isinstance(resp, Response):
                await resp.aclose()
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

    def should_retry_request(self, request: Request) -> bool:
        return default_should_retry_request(request.method)

    def should_retry_response(
        self, request: Request, response: Response | Exception
    ) -> bool:
        return default_should_retry_response(
            request.method,
            response.status if isinstance(response, Response) else response,
        )


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

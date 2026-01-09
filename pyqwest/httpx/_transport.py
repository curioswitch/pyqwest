from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, cast

import httpx

from pyqwest import (
    Headers,
    HTTPTransport,
    Request,
    Response,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class AsyncPyQwestTransport(httpx.AsyncBaseTransport):
    """An HTTPX transport implementation that delegates to pyqwest.

    This can be used with any existing code using httpx.AsyncClient, and will enable
    use of bidirectional streaming and response trailers.
    """

    _transport: HTTPTransport

    def __init__(self, transport: HTTPTransport) -> None:
        """Creates a new AsyncPyQwestTransport.

        Args:
            transport: The pyqwest HTTPTransport to delegate requests to.
        """
        self._transport = transport

    async def handle_async_request(
        self, httpx_request: httpx.Request
    ) -> httpx.Response:
        request_headers = convert_headers(httpx_request.headers)
        request_content = async_request_content(httpx_request.stream)
        timeout = convert_timeout(httpx_request.extensions)

        response = await self._transport.execute(
            Request(
                httpx_request.method,
                str(httpx_request.url),
                headers=request_headers,
                content=request_content,
                timeout=timeout,
            )
        )

        def get_trailers() -> httpx.Headers:
            return httpx.Headers(tuple(response.trailers.items()))

        return httpx.Response(
            status_code=response.status,
            headers=httpx.Headers(tuple(response.headers.items())),
            stream=AsyncIteratorByteStream(response),
            extensions={"get_trailers": get_trailers},
        )


def async_request_content(
    stream: httpx.AsyncByteStream | httpx.SyncByteStream | httpx.ByteStream,
) -> bytes | AsyncIterator[bytes]:
    match stream:
        case httpx.ByteStream():
            # Buffered bytes
            return next(iter(stream))
        case _:
            return async_request_content_iter(stream)


async def async_request_content_iter(
    stream: httpx.AsyncByteStream | httpx.SyncByteStream,
) -> AsyncIterator[bytes]:
    match stream:
        case httpx.AsyncByteStream():
            async with contextlib.aclosing(stream):
                async for chunk in stream:
                    yield chunk
        case httpx.SyncByteStream():
            with contextlib.closing(stream):
                stream_iter = iter(stream)
                while True:
                    chunk = await asyncio.to_thread(next, stream_iter, None)
                    if chunk is None:
                        break
                    yield chunk


class AsyncIteratorByteStream(httpx.AsyncByteStream):
    def __init__(self, response: Response) -> None:
        self._response = response
        self._is_stream_consumed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._is_stream_consumed:
            raise httpx.StreamConsumed
        self._is_stream_consumed = True
        async for chunk in self._response.content:
            yield chunk

    async def aclose(self) -> None:
        await self._response.close()


class PyQwestTransport(httpx.BaseTransport):
    """An HTTPX transport implementation that delegates to pyqwest.

    This can be used with any existing code using httpx.Client, and will enable
    use of bidirectional streaming and response trailers.
    """

    _transport: SyncHTTPTransport

    def __init__(self, transport: SyncHTTPTransport) -> None:
        """Creates a new PyQwestTransport.

        Args:
            transport: The pyqwest HTTPTransport to delegate requests to.
        """
        self._transport = transport

    def handle_request(self, httpx_request: httpx.Request) -> httpx.Response:
        request_headers = convert_headers(httpx_request.headers)
        request_content = sync_request_content(httpx_request.stream)
        timeout = convert_timeout(httpx_request.extensions)

        response = self._transport.execute(
            SyncRequest(
                httpx_request.method,
                str(httpx_request.url),
                headers=request_headers,
                content=request_content,
                timeout=timeout,
            )
        )

        def get_trailers() -> httpx.Headers:
            return httpx.Headers(tuple(response.trailers.items()))

        return httpx.Response(
            status_code=response.status,
            headers=httpx.Headers(tuple(response.headers.items())),
            stream=IteratorByteStream(response),
            extensions={"get_trailers": get_trailers},
        )


def sync_request_content(
    stream: httpx.AsyncByteStream | httpx.SyncByteStream | httpx.ByteStream,
) -> bytes | Iterator[bytes]:
    match stream:
        case httpx.ByteStream():
            # Buffered bytes
            return next(iter(stream))
        case _:
            return sync_request_content_iter(stream)


def sync_request_content_iter(
    stream: httpx.AsyncByteStream | httpx.SyncByteStream,
) -> Iterator[bytes]:
    match stream:
        case httpx.AsyncByteStream():
            msg = "unreachable"
            raise TypeError(msg)
        case httpx.SyncByteStream():
            with contextlib.closing(stream):
                yield from stream


class IteratorByteStream(httpx.SyncByteStream):
    def __init__(self, response: SyncResponse) -> None:
        self._response = response
        self._is_stream_consumed = False

    def __iter__(self) -> Iterator[bytes]:
        if self._is_stream_consumed:
            raise httpx.StreamConsumed
        self._is_stream_consumed = True
        return self._response.content

    def close(self) -> None:
        self._response.close()


# Headers that are managed by the transport and should not be forwarded.
TRANSPORT_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-connection",
    "transfer-encoding",
    "upgrade",
}


def convert_headers(headers: httpx.Headers) -> Headers:
    return Headers(
        (k, v) for k, v in headers.multi_items() if k.lower() not in TRANSPORT_HEADERS
    )


def convert_timeout(extensions: dict) -> float | None:
    httpx_timeout = cast("dict | None", extensions.get("timeout"))
    if httpx_timeout is None:
        return None
    # reqwest does not support setting individual timeout settings
    # per call, only an operation timeout, so we need to approximate
    # that from the httpx timeout dict. Connect usually happens once
    # and can be given a longer timeout - we assume the operation timeout
    # is the max of read/write if present, or connect if not. We ignore
    # pool for now
    read_timeout = httpx_timeout.get("read", -1)
    if read_timeout is None:
        read_timeout = -1
    write_timeout = httpx_timeout.get("write", -1)
    if write_timeout is None:
        write_timeout = -1
    operation_timeout = max(read_timeout, write_timeout)
    if operation_timeout != -1:
        return operation_timeout
    return httpx_timeout.get("connect")

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, cast

import httpx
from h2.errors import ErrorCodes
from h2.events import StreamReset

from pyqwest import (
    Headers,
    ReadError,
    RemoteProtocolError,
    Request,
    Response,
    StreamError,
    StreamErrorCode,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    TooManyRedirects,
    Transport,
    WriteError,
)
from pyqwest._pyqwest import set_sync_timeout

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


class AsyncPyqwestTransport(httpx.AsyncBaseTransport):
    """An HTTPX transport implementation that delegates to pyqwest.

    This can be used with any existing code using httpx.AsyncClient, and will enable
    use of bidirectional streaming and response trailers.

    By default, [pyqwest.HTTPTransport][] follows redirects internally. To have
    HTTPX handle it instead, for example to set `response.history`, configure
    the pyqwest transport with `follow_redirects=False`.
    """

    _transport: Transport

    def __init__(self, transport: Transport) -> None:
        """Creates a new AsyncPyQwestTransport.

        Args:
            transport: The pyqwest transport to delegate requests to.
        """
        self._transport = transport

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        check_scheme(request)
        timeout = convert_timeout(request.extensions)
        deadline = None
        if timeout is not None:
            deadline = asyncio.get_running_loop().time() + timeout

        try:
            pyqwest_request = Request(
                request.method,
                str(request.url),
                headers=convert_headers(request),
                content=async_request_content(request.stream),
            )
        except ValueError as e:
            raise map_value_error(e, request) from e

        try:
            response = await asyncio.wait_for(
                self._transport.execute(pyqwest_request), remaining_time(deadline)
            )
        except StreamError as e:
            # Must precede RemoteProtocolError, which it subclasses, to keep the
            # richer stream error message.
            raise map_stream_error(e) from e
        except RemoteProtocolError as e:
            raise map_remote_protocol_error(e, request) from e
        except TooManyRedirects as e:
            raise httpx.TooManyRedirects(str(e), request=request) from e
        except ConnectionError as e:
            raise map_connection_error(e, request) from e
        except (TimeoutError, asyncio.TimeoutError) as e:
            raise map_timeout_error(e, request) from e
        except (ReadError, WriteError) as e:
            raise map_network_error(e, request) from e

        def get_trailers() -> httpx.Headers:
            return httpx.Headers(tuple(response.trailers.items()))

        return httpx.Response(
            status_code=response.status,
            headers=httpx.Headers(tuple(response.headers.items())),
            stream=AsyncIteratorByteStream(response, deadline),
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
                    yield chunk  # ty: ignore[invalid-yield] # seems to be narrowing bug


class AsyncIteratorByteStream(httpx.AsyncByteStream):
    def __init__(self, response: Response, deadline: float | None = None) -> None:
        self._response = response
        self._deadline = deadline
        self._is_stream_consumed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self._is_stream_consumed:
            raise httpx.StreamConsumed
        self._is_stream_consumed = True
        try:
            if self._deadline is None:
                async for chunk in self._response.content:
                    yield bytes(chunk)
            else:
                # Content is read after handle_async_request returns, so the
                # request timeout can only be applied here, not there.
                content = self._response.content
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            anext(content), remaining_time(self._deadline)
                        )
                    except StopAsyncIteration:
                        break
                    yield bytes(chunk)
        except StreamError as e:
            # Must precede RemoteProtocolError, which it subclasses, to keep the
            # richer stream error message.
            raise map_stream_error(e) from e
        except RemoteProtocolError as e:
            raise map_remote_protocol_error(e) from e
        except ConnectionError as e:
            raise map_connection_error(e) from e
        except (TimeoutError, asyncio.TimeoutError) as e:
            raise map_timeout_error(e) from e
        except (ReadError, WriteError) as e:
            raise map_network_error(e) from e

    async def aclose(self) -> None:
        await self._response.aclose()


class PyqwestTransport(httpx.BaseTransport):
    """An HTTPX transport implementation that delegates to pyqwest.

    This can be used with any existing code using httpx.Client, and will enable
    use of bidirectional streaming and response trailers.

    By default, [pyqwest.SyncHTTPTransport][] follows redirects internally. To have
    HTTPX handle it instead, for example to set `response.history`, configure
    the pyqwest transport with `follow_redirects=False`.
    """

    _transport: SyncTransport

    def __init__(self, transport: SyncTransport) -> None:
        """Creates a new PyQwestTransport.

        Args:
            transport: The pyqwest transport to delegate requests to.
        """
        self._transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        check_scheme(request)
        timeout = convert_timeout(request.extensions)

        try:
            pyqwest_request = SyncRequest(
                request.method,
                str(request.url),
                headers=convert_headers(request),
                content=sync_request_content(request.stream),
            )
        except ValueError as e:
            raise map_value_error(e, request) from e

        timeout_manager = None
        if timeout is not None:
            timeout_manager = set_sync_timeout(timeout)
            timeout_manager.__enter__()

        try:
            response = self._transport.execute_sync(pyqwest_request)
        except StreamError as e:
            # Must precede RemoteProtocolError, which it subclasses, to keep the
            # richer stream error message.
            raise map_stream_error(e) from e
        except RemoteProtocolError as e:
            raise map_remote_protocol_error(e, request) from e
        except TooManyRedirects as e:
            raise httpx.TooManyRedirects(str(e), request=request) from e
        except ConnectionError as e:
            raise map_connection_error(e, request) from e
        except TimeoutError as e:
            raise map_timeout_error(e, request) from e
        except (ReadError, WriteError) as e:
            raise map_network_error(e, request) from e
        finally:
            if timeout_manager is not None:
                timeout_manager.__exit__(None, None, None)

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
    # Some streams, notably MultipartStream, subclass both SyncByteStream and
    # AsyncByteStream, so the sync case must be matched first.
    match stream:
        case httpx.SyncByteStream():
            with contextlib.closing(stream):
                yield from stream
        case httpx.AsyncByteStream():
            msg = "unreachable"
            raise TypeError(msg)


class IteratorByteStream(httpx.SyncByteStream):
    def __init__(self, response: SyncResponse) -> None:
        self._response = response
        self._is_stream_consumed = False

    def __iter__(self) -> Iterator[bytes]:
        if self._is_stream_consumed:
            raise httpx.StreamConsumed
        self._is_stream_consumed = True
        try:
            for chunk in self._response.content:
                yield bytes(chunk)
        except StreamError as e:
            # Must precede RemoteProtocolError, which it subclasses, to keep the
            # richer stream error message.
            raise map_stream_error(e) from e
        except RemoteProtocolError as e:
            raise map_remote_protocol_error(e) from e
        except ConnectionError as e:
            raise map_connection_error(e) from e
        except TimeoutError as e:
            raise map_timeout_error(e) from e
        except (ReadError, WriteError) as e:
            raise map_network_error(e) from e

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


def convert_headers(request: httpx.Request) -> Headers:
    # httpx adds a host header matching the URL to every request, but the
    # transport derives it from the URL itself (:authority on HTTP/2, where a
    # redundant literal host field is rejected by some servers). Only forward
    # host when the user overrode it to a different value. There isn't any
    # way to detect if the user explicitly set the host header at this layer
    # so the best we can do is compare it to the URL.

    # HTTP defines host as case-insensitive
    url_host = request.url.netloc.decode("ascii").lower()
    headers = Headers()
    for name, value in request.headers.multi_items():
        lower_name = name.lower()
        if lower_name in TRANSPORT_HEADERS:
            continue
        if lower_name == "host" and value.lower() == url_host:
            continue
        headers.add(name, value)
    return headers


def check_scheme(request: httpx.Request) -> None:
    # The transport only speaks HTTP, and the underlying client reports anything
    # else as a generic client build failure, so reject it here with the same
    # error httpx uses.
    scheme = request.url.scheme
    if scheme not in ("http", "https"):
        msg = (
            f"Request URL has an unsupported protocol '{scheme}://'."
            if scheme
            else "Request URL is missing an 'http://' or 'https://' protocol."
        )
        raise httpx.UnsupportedProtocol(msg, request=request)


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


def remaining_time(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(deadline - asyncio.get_running_loop().time(), 0.0)


def map_connection_error(
    e: ConnectionError, request: httpx.Request | None = None
) -> httpx.ConnectError | httpx.ConnectTimeout:
    if isinstance(e, TimeoutError):
        return httpx.ConnectTimeout(str(e) or "timed out", request=request)
    return httpx.ConnectError(str(e), request=request)


def map_timeout_error(
    e: BaseException, request: httpx.Request | None = None
) -> httpx.ReadTimeout:
    # Connect timeouts are raised as ConnectTimeout (a ConnectionError) and
    # mapped by map_connection_error. The remaining operation timeout covers
    # read/write without distinguishing which phase expired, so it maps to
    # ReadTimeout to satisfy the httpx.TimeoutException contract.
    return httpx.ReadTimeout(str(e) or "timed out", request=request)


def map_value_error(
    e: ValueError, request: httpx.Request | None = None
) -> httpx.LocalProtocolError:
    # The method, URL or headers were rejected while building the request, so
    # nothing was sent. httpx reports malformed requests as LocalProtocolError.
    return httpx.LocalProtocolError(str(e), request=request)


def map_network_error(
    e: ReadError | WriteError, request: httpx.Request | None = None
) -> httpx.ReadError | httpx.WriteError:
    if isinstance(e, WriteError):
        return httpx.WriteError(str(e), request=request)
    return httpx.ReadError(str(e), request=request)


def map_remote_protocol_error(
    e: RemoteProtocolError, request: httpx.Request | None = None
) -> httpx.RemoteProtocolError:
    # The peer sent something that isn't valid HTTP, or cut a message short.
    return httpx.RemoteProtocolError(str(e), request=request)


def map_stream_error(e: StreamError) -> httpx.RemoteProtocolError:
    match e.code:
        case StreamErrorCode.NO_ERROR:
            code = ErrorCodes.NO_ERROR
        case StreamErrorCode.PROTOCOL_ERROR:
            code = ErrorCodes.PROTOCOL_ERROR
        case StreamErrorCode.INTERNAL_ERROR:
            code = ErrorCodes.INTERNAL_ERROR
        case StreamErrorCode.FLOW_CONTROL_ERROR:
            code = ErrorCodes.FLOW_CONTROL_ERROR
        case StreamErrorCode.SETTINGS_TIMEOUT:
            code = ErrorCodes.SETTINGS_TIMEOUT
        case StreamErrorCode.STREAM_CLOSED:
            code = ErrorCodes.STREAM_CLOSED
        case StreamErrorCode.FRAME_SIZE_ERROR:
            code = ErrorCodes.FRAME_SIZE_ERROR
        case StreamErrorCode.REFUSED_STREAM:
            code = ErrorCodes.REFUSED_STREAM
        case StreamErrorCode.CANCEL:
            code = ErrorCodes.CANCEL
        case StreamErrorCode.COMPRESSION_ERROR:
            code = ErrorCodes.COMPRESSION_ERROR
        case StreamErrorCode.CONNECT_ERROR:
            code = ErrorCodes.CONNECT_ERROR
        case StreamErrorCode.ENHANCE_YOUR_CALM:
            code = ErrorCodes.ENHANCE_YOUR_CALM
        case StreamErrorCode.INADEQUATE_SECURITY:
            code = ErrorCodes.INADEQUATE_SECURITY
        case StreamErrorCode.HTTP_1_1_REQUIRED:
            code = ErrorCodes.HTTP_1_1_REQUIRED
        case _:
            code = ErrorCodes.INTERNAL_ERROR
    return httpx.RemoteProtocolError(str(StreamReset(stream_id=-1, error_code=code)))

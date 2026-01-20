from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Empty, Queue
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from pyqwest import (
    Headers,
    HTTPVersion,
    ReadError,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    WriteError,
)
from pyqwest._pyqwest import get_sync_timeout

from ._decompress import Decompressor, get_decompressor

if TYPE_CHECKING:
    import sys

    if sys.version_info >= (3, 11):
        from wsgiref.types import WSGIApplication, WSGIEnvironment
    else:
        from _typeshed.wsgi import WSGIApplication, WSGIEnvironment

_UNSET_STATUS = "unset"

_DEFAULT_EXECUTOR: ThreadPoolExecutor | None = None


def get_default_executor() -> ThreadPoolExecutor:
    global _DEFAULT_EXECUTOR  # noqa: PLW0603
    if _DEFAULT_EXECUTOR is None:
        _DEFAULT_EXECUTOR = ThreadPoolExecutor()
    return _DEFAULT_EXECUTOR


class WSGITransport(SyncTransport):
    """Transport implementation that directly invokes a WSGI application. Useful for testing."""

    _app: WSGIApplication
    _http_version: HTTPVersion
    _closed: bool
    _app_exception: Exception | None

    def __init__(
        self,
        app: WSGIApplication,
        http_version: HTTPVersion = HTTPVersion.HTTP2,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        """Creates a new WSGI transport.

        Args:
            app: The WSGI application to invoke for requests.
            http_version: The HTTP version to simulate for requests.
            executor: An optional ThreadPoolExecutor to use for running the WSGI app.
                      If not provided, a default executor will be used.
        """
        self._app = app
        self._http_version = http_version
        self._executor = executor or get_default_executor()
        self._closed = False

    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        timeout = get_sync_timeout()
        deadline = None
        if timeout is not None:
            deadline = time.monotonic() + timeout.total_seconds()

        parsed_url = urlparse(request.url)
        raw_path = parsed_url.path or "/"
        path = unquote(raw_path).encode().decode("latin-1")
        query = parsed_url.query.encode().decode("latin-1")

        match self._http_version:
            case HTTPVersion.HTTP1:
                server_protocol = "HTTP/1.1"
            case HTTPVersion.HTTP2:
                server_protocol = "HTTP/2"
            case HTTPVersion.HTTP3:
                server_protocol = "HTTP/3"
            case _:
                server_protocol = "HTTP/1.1"

        trailers = Headers()
        trailers_supported = (
            self._http_version == HTTPVersion.HTTP2
            and request.headers.get("te", "") == "trailers"
        )

        def send_trailers(headers: list[tuple[str, str]]) -> None:
            if not trailers_supported:
                return
            for k, v in headers:
                trailers.add(k, v)

        request_input = RequestInput(request.content, self._http_version)
        environ: WSGIEnvironment = {
            "REQUEST_METHOD": request.method,
            "SCRIPT_NAME": "",
            "PATH_INFO": path,
            "QUERY_STRING": query,
            "SERVER_NAME": parsed_url.hostname or "",
            "SERVER_PORT": str(
                parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
            ),
            "SERVER_PROTOCOL": server_protocol,
            "wsgi.url_scheme": parsed_url.scheme,
            "wsgi.version": (1, 0),
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "wsgi.input": request_input,
            "wsgi.ext.http.send_trailers": send_trailers,
        }

        for k, v in request.headers.items():
            match k:
                case "content-type":
                    environ["CONTENT_TYPE"] = v
                case "content-length":
                    environ["CONTENT_LENGTH"] = v
                case _:
                    name = f"HTTP_{k.upper().replace('-', '_')}"
                    value = f"{existing},{v}" if (existing := environ.get(name)) else v
                    environ[name] = value

        response_queue: Queue[bytes | None | Exception] = Queue()

        status_str: str = _UNSET_STATUS
        headers: list[tuple[str, str]] = []
        exc: (
            tuple[type[BaseException], BaseException, object]
            | tuple[None, None, None]
            | None
        ) = None
        response_started = threading.Event()

        def start_response(
            status: str,
            response_headers: list[tuple[str, str]],
            exc_info: tuple[type[BaseException], BaseException, object]
            | tuple[None, None, None]
            | None = None,
        ) -> Callable[[bytes], object]:
            nonlocal status_str, headers, exc
            status_str = status
            headers = response_headers
            exc = exc_info

            def write(body: bytes) -> None:
                if not response_started.is_set():
                    response_started.set()
                if body:
                    response_queue.put(body)

            return write

        def run_app() -> None:
            response_iter = self._app(environ, start_response)
            try:
                for chunk in response_iter:
                    if chunk:
                        if not response_started.is_set():
                            response_started.set()
                        response_queue.put(chunk)
            except Exception as e:
                self._app_exception = e
                response_queue.put(e)
            else:
                response_queue.put(None)
            finally:
                if not response_started.is_set():
                    request_input.close()
                    response_started.set()
                with contextlib.suppress(Exception):
                    response_iter.close()  # pyright: ignore[reportAttributeAccessIssue]

        app_future = self._executor.submit(run_app)

        if not response_started.wait(
            timeout=timeout.total_seconds() if timeout is not None else None
        ):
            request_input.close()
            msg = "Application did not start response before timeout"
            raise WSGITimeoutError(msg, app_future)

        if status_str is _UNSET_STATUS:
            return SyncResponse(
                status=500,
                http_version=self._http_version,
                headers=Headers((("content-type", "text/plain"),)),
                content=b"WSGI application did not call start_response",
            )

        if exc and exc[0]:
            return SyncResponse(
                status=500,
                http_version=self._http_version,
                headers=Headers((("content-type", "text/plain"),)),
                content=str(exc[0]).encode(),
            )

        response_headers = Headers(headers)
        decompressor = get_decompressor(response_headers.get("content-encoding"))
        response_content = ResponseContent(
            response_queue, request_input, app_future, deadline, decompressor
        )

        status = int(status_str.split(" ", 1)[0])

        return SyncResponse(
            status=status,
            headers=response_headers,
            http_version=self._http_version,
            content=response_content,
            trailers=trailers,
        )

    @property
    def app_exception(self) -> Exception | None:
        """The exception raised by the ASGI application, if any.

        This will be overwritten for any request which raises an exception, so it is generally
        expected to be used with a transport that is used only once, or in a precise order.
        """
        return self._app_exception


class RequestInput:
    def __init__(self, content: Iterator[bytes], http_version: HTTPVersion) -> None:
        self._content = content
        self._http_version = http_version
        self._closed = False
        self._buffer = bytearray()

    def read(self, size: int = -1) -> bytes:
        return self._do_read(size)

    def readline(self, size: int = -1) -> bytes:
        if self._closed or size == 0:
            return b""

        line = bytearray()
        while True:
            sz = size - len(line) if size >= 0 else -1
            read_bytes = self._do_read(sz)
            if not read_bytes:
                return bytes(line)
            if len(line) + len(read_bytes) == size:
                return bytes(line + read_bytes)
            newline_index = read_bytes.find(b"\n")
            if newline_index == -1:
                line.extend(read_bytes)
                continue
            res = line + read_bytes[: newline_index + 1]
            self._buffer.extend(read_bytes[newline_index + 1 :])
            return bytes(res)

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def readlines(self, hint: int = -1) -> list[bytes]:
        return list(self)

    def _do_read(self, size: int) -> bytes:
        if self._closed or size == 0:
            return b""

        try:
            while True:
                chunk = next(self._content)
                if size < 0:
                    self._buffer.extend(chunk)
                    continue
                if len(self._buffer) + len(chunk) >= size:
                    to_read = size - len(self._buffer)
                    res = self._buffer + chunk[:to_read]
                    self._buffer.clear()
                    self._buffer.extend(chunk[to_read:])
                    return bytes(res)
                if len(self._buffer) == 0:
                    return chunk
                res = self._buffer + chunk
                self._buffer.clear()
                return bytes(res)
        except StopIteration:
            self.close()
            res = bytes(self._buffer)
            self._buffer = bytearray()
            return res
        except Exception as e:
            self.close()
            if self._http_version != HTTPVersion.HTTP2:
                msg = f"Request failed: {e}"
            else:
                # With HTTP/2, reqwest seems to squash the original error message.
                msg = "Request failed: stream error sent by user"
            raise WriteError(msg) from e

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            self._content.close()  # pyright: ignore[reportAttributeAccessIssue]


class ResponseContent(Iterator[bytes]):
    def __init__(
        self,
        response_queue: Queue[bytes | None | Exception],
        request_input: RequestInput,
        app_future: Future,
        deadline: float | None,
        decompressor: Decompressor,
    ) -> None:
        self._response_queue = response_queue
        self._request_input = request_input
        self._app_future = app_future
        self._closed = False
        self._read_pending = False
        self._deadline = deadline
        self._decompressor = decompressor

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        err: Exception | None = None
        self._read_pending = True
        chunk = b""
        try:
            if self._deadline:
                while True:
                    time_left = self._deadline - time.monotonic()
                    if time_left <= 0:
                        msg = "Response read timed out"
                        message = TimeoutError(msg)
                        break
                    try:
                        message = self._response_queue.get(timeout=time_left)
                        break
                    except Empty:
                        continue
            else:
                message = self._response_queue.get()
        finally:
            self._read_pending = False
        if isinstance(message, Exception):
            match message:
                case WriteError() | TimeoutError():
                    err = message
                case _:
                    msg = "Request Failed: Error reading response body"
                    err = ReadError(msg)
        elif message is None:
            remaining = self._decompressor.feed(b"", end=True)
            if remaining:
                self._closed = True
                self._request_input.close()
                with contextlib.suppress(Exception):
                    self._app_future.result()
                return remaining
            err = StopIteration()
        else:
            chunk = message

        if err:
            self._closed = True
            self._request_input.close()
            with contextlib.suppress(Exception):
                self._app_future.result()
            raise err
        return self._decompressor.feed(chunk, end=False)

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._request_input.close()
        self._response_queue.put(ReadError("Response body read cancelled"))
        with contextlib.suppress(Exception):
            self._app_future.result()


class WSGITimeoutError(TimeoutError):
    """Timeout error raised by WSGI transport.

    Contains a handle to the app future to allow joining on its thread.
    """

    def __init__(self, msg: str, app_future: Future) -> None:
        super().__init__(msg)
        self._app_future = app_future

    def wait(self, timeout: float | None = None) -> None:
        """Waits for the WSGI application to finish."""
        with contextlib.suppress(Exception):
            self._app_future.result(timeout)

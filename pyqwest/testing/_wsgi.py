from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Iterator
from queue import Queue
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pyqwest import (
    Headers,
    HTTPVersion,
    ReadError,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    WriteError,
)

if TYPE_CHECKING:
    if sys.version_info >= (3, 11):
        from wsgiref.types import InputStream as WSGIInputStream
        from wsgiref.types import WSGIApplication, WSGIEnvironment
    else:
        from _typeshed.wsgi import InputStream as WSGIInputStream
        from _typeshed.wsgi import WSGIApplication, WSGIEnvironment


class WSGITransport(SyncTransport):
    _app: WSGIApplication
    _http_version: HTTPVersion

    def __init__(
        self, app: WSGIApplication, http_version: HTTPVersion = HTTPVersion.HTTP2
    ) -> None:
        self._app = app
        self._http_version = http_version

    def execute(self, request: SyncRequest) -> SyncResponse:
        parsed_url = urlparse(request.url)
        path = (parsed_url.path or "/").encode().decode("latin-1")
        query = parsed_url.query.encode().decode("latin-1")

        match self._http_version:
            case HTTPVersion.HTTP1:
                server_protocol = "HTTP/1.1"
            case HTTPVersion.HTTP2:
                server_protocol = "HTTP/2"
            case HTTPVersion.HTTP3:
                server_protocol = "HTTP/3"

        trailers = Headers()

        def send_trailers(headers: list[tuple[str, str]]) -> None:
            for k, v in headers:
                trailers.add(k, v)

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
            "wsgi.input": RequestInput(request.content),
            "wsgi.ext.http.send_trailers": send_trailers,
        }

        for k, v in request.headers.items():
            match k:
                case "content-type":
                    environ["CONTENT_TYPE"] = v
                case "content-length":
                    environ["CONTENT_LENGTH"] = v
                case _:
                    environ[f"HTTP_{k.upper().replace('-', '_')}"] = v

        response_queue: Queue[bytes | Exception] = Queue()
        response_content = ResponseContent(response_queue)

        start_response_queue: Queue[tuple[str, list[tuple[str, str]]]] = Queue()

        def start_response(
            status: str,
            response_headers: list[tuple[str, str]],
            exc_info: tuple[type[BaseException], BaseException, object] | None = None,
        ) -> None:
            start_response_queue.put_nowait((status, response_headers))

        # Easiest way to support timeout is to use asyncio even for sync transport.
        # If this causes problems for users, we can revisit in the future.
        def run_app() -> None:
            pass

        async def app_task() -> None:
            await asyncio.to_thread(run_app)

        def start_app_task() -> None:
            asyncio.run(app_task())

        app_thread = threading.Thread(target=start_app_task)
        app_thread.start()


class RequestInput(WSGIInputStream):
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
                return line + read_bytes
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
                    continue
                if len(self._buffer) + len(chunk) >= size:
                    to_read = size - len(self._buffer)
                    res = self._buffer + chunk[:to_read]
                    self._buffer.clear()
                    self._buffer.extend(chunk[to_read:])
                    return res
                if len(self._buffer) == 0:
                    return chunk
                res = self._buffer + chunk
                self._buffer.clear()
                return bytes(res)
        except StopIteration:
            self._closed = True
            res = bytes(self._buffer)
            self._buffer = bytearray()
            return res
        except Exception as e:
            self._closed = True
            if self._http_version != HTTPVersion.HTTP2:
                msg = f"Request failed: {chunk}"
            else:
                # With HTTP/2, reqwest seems to squash the original error message.
                msg = "Request failed: stream error sent by user"
            raise WriteError(msg) from e


class CancelResponse(Exception):
    pass


class ResponseContent(Iterator[bytes]):
    def __init__(
        self, response_queue: Queue[bytes | Exception], app_thread: threading.Thread
    ) -> None:
        self._response_queue = response_queue
        self._app_thread = app_thread
        self._closed = False
        self._read_pending = False

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        err: Exception | None = None
        self._read_pending = True
        chunk = b""
        try:
            message = self._response_queue.get()
        finally:
            self._read_pending = False
        if isinstance(message, Exception):
            match message:
                case CancelResponse():
                    err = StopIteration()
                case WriteError() | TimeoutError():
                    err = message
                case _:
                    msg = "Error reading response body"
                    err = ReadError(msg)
        else:
            chunk = message

        if err:
            self._closed = True
            raise err
        if not message:
            self._closed = True
            raise StopIteration
        return chunk

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._response_queue.put_nowait(CancelResponse())

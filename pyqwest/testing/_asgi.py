from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import unquote, urlparse

from pyqwest import (
    Headers,
    HTTPVersion,
    ReadError,
    Request,
    Response,
    Transport,
    WriteError,
)
from pyqwest._runtime import current_runtime

from ._asgi_compatibility import guarantee_single_callable
from ._decompress import Decompressor, get_decompressor

if TYPE_CHECKING:
    from types import TracebackType

    from asgiref.typing import (
        ASGI3Application,
        ASGIApplication,
        ASGIReceiveEvent,
        ASGISendEvent,
        ASGIVersions,
        HTTPScope,
        LifespanScope,
        LifespanShutdownEvent,
        LifespanStartupEvent,
    )

    from pyqwest._runtime import Queue, Task

_asgi: ASGIVersions = {"version": "3.0", "spec_version": "2.5"}
_extensions = {"http.response.trailers": {}}


@dataclass(frozen=True)
class Lifespan:
    task: Task
    receive_queue: Queue[LifespanStartupEvent | LifespanShutdownEvent]
    send_queue: Queue[ASGISendEvent | Exception]


class ASGITransport(Transport):
    """Transport implementation that directly invokes an ASGI application. Useful for testing.

    The ASGI transport supports lifespan - to use it, make sure to use the transport as an
    asynchronous context manager. Lifespan startup will be run on entering and shutdown when
    exiting.
    """

    _app: ASGI3Application
    _http_version: HTTPVersion
    _client: tuple[str, int]
    _state: dict[str, Any]
    _lifespan: Lifespan | None
    _app_exception: Exception | None

    def __init__(
        self,
        app: ASGIApplication,
        http_version: HTTPVersion = HTTPVersion.HTTP2,
        client: tuple[str, int] = ("127.0.0.1", 111),
    ) -> None:
        """Creates a new ASGI transport.

        Args:
            app: The ASGI application to invoke.
            http_version: The HTTP version to mimic for requests. Note, semantics such as lack of
                          bidirectional streaming for HTTP/1 are not enforced.
            client: The (host, port) tuple to use for the client address in the ASGI scope.
        """
        self._app = guarantee_single_callable(app)
        self._http_version = http_version
        self._client = client
        self._state = {}
        self._lifespan = None
        self._app_exception = None

    async def execute(self, request: Request) -> Response:
        parsed_url = urlparse(request.url)
        raw_path = parsed_url.path or "/"
        path = unquote(raw_path)
        match self._http_version:
            case HTTPVersion.HTTP1:
                http_version = "1.1"
            case HTTPVersion.HTTP2:
                http_version = "2"
            case HTTPVersion.HTTP3:
                http_version = "3"
            case _:
                http_version = "1.1"
        request_headers = Headers(request.headers.items())
        if "host" not in request_headers:
            request_headers["host"] = parsed_url.netloc
        if request._json and "content-type" not in request_headers:  # noqa: SLF001
            request_headers["content-type"] = "application/json"

        scope: HTTPScope = {
            "type": "http",
            "asgi": _asgi,
            "http_version": http_version,
            "method": request.method,
            "scheme": parsed_url.scheme,
            "path": path,
            "raw_path": raw_path.encode(),
            "query_string": parsed_url.query.encode(),
            "headers": [
                (k.lower().encode("utf-8"), v.encode("utf-8"))
                for k, v in request_headers.items()
            ],
            "server": (
                parsed_url.hostname or "",
                parsed_url.port or (443 if parsed_url.scheme == "https" else 80),
            ),
            "client": self._client,
            "extensions": _extensions,
            "state": self._state,
            "root_path": "",
        }

        runtime = current_runtime()
        receive_queue: Queue[bytes | Exception | None] = runtime.new_queue(1)
        receive_started = runtime.new_event()
        request_body = RequestBody()

        async def read_request_content() -> None:
            try:
                await receive_started.wait()
                if isinstance(request.content, bytes):
                    await receive_queue.put(request.content)
                    await receive_queue.put(None)
                    return
                async for chunk in request.content:
                    if not isinstance(chunk, bytes):
                        msg = "Request not bytes object"
                        raise WriteError(msg)  # noqa: TRY301
                    await receive_queue.put(chunk)
                await receive_queue.put(None)
            except Exception as e:
                request_body.exception = e
                await receive_queue.put(e)
            finally:
                try:
                    aclose = request.content.aclose  # ty: ignore[unresolved-attribute]
                except AttributeError:
                    pass
                else:
                    # The task closes the request content even when it was
                    # cancelled, so the shield keeps the close from failing.
                    with runtime.shield():
                        await aclose()

        # Need a separate task to read the request body to allow
        # cancelling when response closes.
        request_task = runtime.spawn(read_request_content, "pyqwest ASGI request body")

        async def receive() -> ASGIReceiveEvent:
            receive_started.set()
            chunk = await receive_queue.get()
            if chunk is None:
                return {"type": "http.request", "body": b"", "more_body": False}
            if isinstance(chunk, BaseException):
                raise chunk
            return {"type": "http.request", "body": chunk, "more_body": True}

        send_queue: Queue[ASGISendEvent | Exception] = runtime.new_queue()

        async def send(message: ASGISendEvent) -> None:
            await send_queue.put(message)

        async def run_app() -> None:
            try:
                await self._app(scope, receive, send)
            except TimeoutError as e:
                send_queue.put_nowait(TimeoutError(str(e)))
            except Exception as e:
                self._app_exception = e
                send_queue.put_nowait(e)

        app_task = runtime.spawn(run_app, "pyqwest ASGI app")
        try:
            message = await send_queue.get()
        except BaseException:
            request_task.cancel()
            app_task.cancel()
            raise
        if isinstance(message, Exception):
            request_task.cancel()
            await app_task.wait()
            await request_task.wait()
            if message is request_body.exception or isinstance(
                message, (ConnectionError, TimeoutError)
            ):
                raise message
            return Response(
                status=500,
                http_version=self._http_version,
                headers=Headers((("content-type", "text/plain"),)),
                content=str(message).encode(),
            )

        assert message["type"] == "http.response.start"  # noqa: S101
        status = message["status"]
        headers = Headers(
            (
                (k.decode("utf-8"), v.decode("utf-8"))
                for k, v in message.get("headers", [])
            )
        )
        trailers = (
            Headers()
            if self._http_version == HTTPVersion.HTTP2
            and request.headers.get("te") == "trailers"
            else None
        )

        decompressor = get_decompressor(headers.get("content-encoding"))
        response_content = ResponseContent(
            send_queue,
            request_task,
            request_body,
            trailers,
            app_task,
            decompressor,
            read_trailers=message.get("trailers", False),
        )
        return Response(
            status=status,
            http_version=self._http_version,
            headers=headers,
            content=response_content,
            trailers=trailers,
        )

    async def __aenter__(self) -> Self:
        await self.run_lifespan()
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def run_lifespan(self) -> None:
        scope: LifespanScope = {"type": "lifespan", "asgi": _asgi, "state": self._state}

        runtime = current_runtime()
        receive_queue: Queue[LifespanStartupEvent | LifespanShutdownEvent] = (
            runtime.new_queue()
        )

        async def receive() -> LifespanStartupEvent | LifespanShutdownEvent:
            return await receive_queue.get()

        send_queue: Queue[ASGISendEvent | Exception] = runtime.new_queue()

        async def send(message: ASGISendEvent) -> None:
            await send_queue.put(message)

        async def run_app() -> None:
            try:
                await self._app(scope, receive, send)
            except Exception as e:
                send_queue.put_nowait(e)

        task = runtime.spawn(run_app, "pyqwest ASGI lifespan")

        receive_queue.put_nowait({"type": "lifespan.startup"})
        message = await send_queue.get()
        if isinstance(message, Exception):
            # Lifespan not supported
            await task.wait()
            return

        self._lifespan = Lifespan(
            task=task, receive_queue=receive_queue, send_queue=send_queue
        )
        match message["type"]:
            case "lifespan.startup.complete":
                return
            case "lifespan.startup.failed":
                msg = (
                    f"ASGI application failed to start up: {message.get('message', '')}"
                )
                raise RuntimeError(msg)

    async def close(self) -> None:
        if self._lifespan is None:
            return

        await self._lifespan.receive_queue.put({"type": "lifespan.shutdown"})
        message = await self._lifespan.send_queue.get()
        await self._lifespan.task.wait()
        if isinstance(message, Exception):
            raise message
        match message["type"]:
            case "lifespan.shutdown.complete":
                return
            case "lifespan.shutdown.failed":
                msg = f"ASGI application failed to shut down cleanly: {message.get('message', '')}"
                raise RuntimeError(msg)

    @property
    def app_exception(self) -> Exception | None:
        """The exception raised by the ASGI application, if any.

        This will be overwritten for any request which raises an exception, so it is generally
        expected to be used with a transport that is used only once, or in a precise order.
        """
        return self._app_exception


class CancelResponse(Exception):
    pass


@dataclass
class RequestBody:
    """Holds the exception the request body raised, if any."""

    exception: Exception | None = None


class ResponseContent(AsyncIterator[bytes]):
    def __init__(
        self,
        send_queue: Queue[ASGISendEvent | Exception],
        request_task: Task,
        request_body: RequestBody,
        trailers: Headers | None,
        task: Task,
        decompressor: Decompressor,
        *,
        read_trailers: bool,
    ) -> None:
        self._send_queue = send_queue
        self._request_task = request_task
        self._request_body = request_body
        self._trailers = trailers
        self._task = task
        self._decompressor = decompressor
        self._read_trailers = read_trailers

        self._read_pending = False
        self._closed = False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        if self._closed:
            raise StopAsyncIteration
        err: Exception | None = None
        body: bytes | None = None
        while True:
            self._read_pending = True
            try:
                message = await self._send_queue.get()
            finally:
                self._read_pending = False
            if isinstance(message, Exception):
                if message is self._request_body.exception:
                    err = message
                    break
                match message:
                    case CancelResponse():
                        err = StopAsyncIteration()
                        break
                    case WriteError() | TimeoutError():
                        err = message
                        break
                    case ReadError():
                        raise message
                    case Exception():
                        msg = "Error reading response body"
                        raise ReadError(msg) from message
            match message["type"]:
                case "http.response.body":
                    more_body = message.get("more_body", False)
                    if not more_body and not self._read_trailers:
                        await self._cleanup()
                    if (body := message.get("body", b"")) or self._closed:
                        return self._decompressor.feed(body, end=not more_body)
                case "http.response.trailers":
                    if self._trailers is not None:
                        for k, v in message.get("headers", []):
                            self._trailers.add(k.decode("utf-8"), v.decode("utf-8"))
                    if not message.get("more_trailers", False):
                        break
        await self._cleanup()
        if err:
            raise err
        raise StopAsyncIteration

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._send_queue.put_nowait(ReadError("Response body read cancelled"))
        await self._cleanup()

    async def _cleanup(self) -> None:
        self._closed = True
        self._request_task.cancel()
        self._task.cancel()
        await self._request_task.wait()
        await self._task.wait()

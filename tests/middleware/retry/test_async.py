from __future__ import annotations

import asyncio
import contextlib
import gc
import sys
from email.utils import formatdate
from time import monotonic, time
from typing import TYPE_CHECKING, cast

import anyio
import anyio.lowlevel
import pytest

from pyqwest import Client, HTTPTransport, ReadError, Request, Response, WriteError
from pyqwest import Transport as BaseTransport
from pyqwest.middleware.retry import RetryMode, RetryTransport
from pyqwest.middleware.retry._async import RetryingRequestContent
from pyqwest.testing import ASGITransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.abc import TaskGroup
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope


# RetryTransport waits between attempts with asyncio.sleep.
pytestmark = pytest.mark.asyncio_only


class App:
    def __init__(self):
        self.status = [200]
        self.retry_after = ""
        self.read_content = b""
        self.count = 0
        self.timeouts = 0
        self.connection_errors = 0

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "http":
            return
        self.count += 1
        if self.timeouts > 0:
            self.timeouts -= 1
            raise TimeoutError
        if self.connection_errors > 0:
            self.connection_errors -= 1
            raise ConnectionError
        content = b""
        while True:
            message = await receive()
            if message["type"] == "http.request":
                content += message.get("body", b"")
                if not message.get("more_body", False):
                    break
        self.read_content = content
        headers = []
        if self.retry_after:
            headers.append((b"retry-after", self.retry_after.encode("utf-8")))
        try:
            status = self.status.pop(0)
        except IndexError:
            status = 500
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": headers,
                "trailers": False,
            }
        )
        await send(
            {"type": "http.response.body", "body": b"response", "more_body": False}
        )


class ConfiguredRetryTransport(RetryTransport):
    def __init__(self, transport: BaseTransport, mode: bool | RetryMode) -> None:
        self._mode = mode
        super().__init__(
            transport,
            initial_interval=0.01,
            randomization_factor=0.0,
            multiplier=3.0,
            max_interval=0.05,
        )

    def should_retry_request(self, request: Request) -> bool | RetryMode:
        return self._mode


@pytest.fixture
def app():
    return App()


@pytest.fixture
def client(app: App):
    return Client(
        RetryTransport(
            ASGITransport(app),
            initial_interval=0.01,
            randomization_factor=0.0,
            multiplier=3.0,
            max_interval=0.05,
        )
    )


def assert_duration_at_least(start: float, end: float, expected: float) -> None:
    if sys.platform == "win32" and sys.version_info < (3, 11):
        # On Windows with Python <= 3.10, at least, timer has too low resolution.
        return
    duration = end - start
    assert duration >= expected, f"Duration {duration} is less than expected {expected}"


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("GET", RetryMode.BUFFERED),
        ("HEAD", RetryMode.BUFFERED),
        ("PUT", RetryMode.BUFFERED),
        ("DELETE", RetryMode.BUFFERED),
        ("POST", RetryMode.UNBUFFERED),
    ],
)
def test_default_retry_mode(app: App, method: str, expected: RetryMode) -> None:
    assert (
        RetryTransport(ASGITransport(app)).should_retry_request(
            Request(method, "http://localhost")
        )
        is expected
    )


@pytest.mark.anyio
async def test_success(app: App, client: Client) -> None:
    res = await client.get("http://localhost")
    assert res.status == 200
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.anyio
async def test_one_retry(app: App, client: Client) -> None:
    app.status = [500, 200]
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01)


@pytest.mark.anyio
async def test_not_retryable_request(app: App, client: Client) -> None:
    app.status = [500, 200]
    res = await client.post("http://localhost", content=b"hello")
    assert res.status == 500
    assert app.count == 1
    assert app.read_content == b"hello"


@pytest.mark.anyio
async def test_not_retryable_response(app: App, client: Client) -> None:
    app.status = [404, 200]
    res = await client.get("http://localhost")
    assert res.status == 404
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.anyio
async def test_not_retryable_response_501(app: App, client: Client) -> None:
    app.status = [501, 200]
    res = await client.get("http://localhost")
    assert res.status == 501
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.anyio
async def test_max_retries(app: App, client: Client) -> None:
    app.status = [500, 502, 503, 504, 200]
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.anyio
async def test_exceed_max_retries(app: App, client: Client) -> None:
    app.status = [500, 502, 503, 504, 505, 200]
    start = monotonic()
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        await client.get("http://localhost")
    end = monotonic()
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.anyio
async def test_retry_fixed_content(app: App, client: Client) -> None:
    content = b"Hello world!"
    app.status = [500, 200]
    res = await client.put("http://localhost", content=content)
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == content


@pytest.mark.anyio
async def test_retry_content_iterator(app: App, client: Client) -> None:
    async def content():
        yield b"Hello "
        yield b"world!"

    app.status = [500, 200]
    res = await client.put("http://localhost", content=content())
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_content_iterator_error_not_retried(app: App, client: Client) -> None:
    async def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    # The ASGI transport reports the body's error as a 500 from the app. A retry
    # would send only the content read before the error, and succeed.
    app.status = [200]
    res = await client.put("http://localhost", content=content())
    assert res.status == 500
    assert app.count == 1


@pytest.mark.anyio
async def test_transport_body_error_not_retried() -> None:
    class Transport(BaseTransport):
        def __init__(self) -> None:
            self.count = 0

        async def execute(self, request: Request) -> Response:
            self.count += 1
            assert not isinstance(request.content, bytes)
            try:
                async for _ in request.content:
                    pass
            except ValueError as e:
                msg = "Request failed"
                raise WriteError(msg) from e
            return Response(status=200, content=b"")

    async def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    transport = Transport()
    client = Client(
        RetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
    )
    with pytest.raises(WriteError):
        await client.put("http://localhost", content=content())
    assert transport.count == 1


@pytest.mark.anyio
async def test_streamed_content_without_retry() -> None:
    class Transport(BaseTransport):
        def __init__(self) -> None:
            self.read_content = b""

        async def execute(self, request: Request) -> Response:
            assert not isinstance(request.content, bytes)
            async for chunk in request.content:
                self.read_content += chunk
            return Response(status=200, content=b"")

    async def content():
        yield b"Hello "
        await anyio.lowlevel.checkpoint()
        yield b"world!"

    transport = Transport()
    res = await Client(RetryTransport(transport)).put(
        "http://localhost", content=content()
    )
    assert res.status == 200
    assert transport.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_retrying_content_error() -> None:
    async def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    retrying = RetryingRequestContent(content())
    with pytest.raises(ValueError, match="boom"):
        async for _ in retrying.get():
            pass
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        await anext(retrying.get())


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["response", "error"])
async def test_content_read_in_progress_retried(outcome: str) -> None:
    reading, resume = anyio.Event(), anyio.Event()

    async def content():
        yield b"Hello "
        reading.set()
        await resume.wait()
        yield b"world!"

    class Transport(BaseTransport):
        def __init__(self, tg: TaskGroup) -> None:
            self.count = 0
            self.read_content = b""
            self._tg = tg

        async def execute(self, request: Request) -> Response:
            self.count += 1
            assert not isinstance(request.content, bytes)
            if self.count > 1:
                resume.set()
                async for chunk in request.content:
                    self.read_content += chunk
                return Response(status=200, content=b"")
            # A server can respond, or the connection fail, while the transport
            # waits for the next chunk. The transport then cancels the body.
            body = anyio.CancelScope()
            self._tg.start_soon(self._read_body, request.content, body)
            await reading.wait()
            body.cancel()
            if outcome == "error":
                msg = "Connection reset"
                raise ReadError(msg)
            return Response(status=503, content=b"")

        async def _read_body(
            self, content: AsyncIterator[bytes], scope: anyio.CancelScope
        ) -> None:
            with scope:
                async for _ in content:
                    pass

    async with anyio.create_task_group() as tg:
        transport = Transport(tg)
        client = Client(
            RetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
        )
        res = await client.put("http://localhost", content=content())
    assert res.status == 200
    assert transport.count == 2
    assert transport.read_content == b"Hello world!"


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["response", "reset"])
async def test_http_attempt_fails_before_content_read(outcome: str) -> None:
    reading, resume = asyncio.Event(), asyncio.Event()
    requests: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await reader.readuntil(b"\r\n\r\n")
        if not requests:
            # Unread content would turn the response into a reset.
            requests.append(await reader.readuntil(b"Hello \r\n"))
            await reading.wait()
            if outcome == "reset":
                writer.transport.abort()
                return
            writer.write(b"HTTP/1.1 503 X\r\ncontent-length: 0\r\n\r\n")
        else:
            resume.set()
            requests.append(await reader.readuntil(b"\r\n0\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    async def content():
        yield b"Hello "
        # The first attempt is waiting for the next chunk when it fails.
        reading.set()
        await resume.wait()
        yield b"world!"

    def without_chunk_framing(body: bytes) -> bytes:
        content = b""
        while body:
            size, _, body = body.partition(b"\r\n")
            content += body[: int(size, 16)]
            body = body[int(size, 16) + 2 :]
        return content

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server, HTTPTransport() as transport:
        client = Client(
            RetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
        )
        with anyio.fail_after(10):
            res = await client.put(f"http://127.0.0.1:{port}", content=content())
    assert res.status == 200
    assert [without_chunk_framing(body) for body in requests] == [
        b"Hello ",
        b"Hello world!",
    ]


@pytest.mark.anyio
async def test_retrying_content_cancelled_mid_read() -> None:
    # An abandoned attempt is cancelled, possibly while it waits for the
    # content's next chunk.
    reading, resume = anyio.Event(), anyio.Event()

    async def content():
        yield b"Hello "
        reading.set()
        await resume.wait()
        yield b"world!"

    retrying = RetryingRequestContent(content())

    async def read() -> None:
        async for _ in retrying.get():
            pass

    async with anyio.create_task_group() as tg:
        tg.start_soon(read)
        await reading.wait()
        tg.cancel_scope.cancel()
    assert retrying.retryable
    resume.set()
    assert [chunk async for chunk in retrying.get()] == [b"Hello ", b"world!"]


@pytest.mark.anyio
async def test_retrying_content_read_in_progress() -> None:
    reading, resume = anyio.Event(), anyio.Event()
    read_from_source: list[bytes] = []

    async def content():
        for chunk in (b"Hello ", b"world", b"!"):
            if chunk == b"world":
                reading.set()
                await resume.wait()
            read_from_source.append(chunk)
            yield chunk

    retrying = RetryingRequestContent(content())
    chunks: list[bytes] = []

    async def read() -> None:
        attempt = retrying.get()
        chunks.extend([await anext(attempt), await anext(attempt)])

    async with anyio.create_task_group() as tg:
        tg.start_soon(read)
        await reading.wait()
        assert retrying.retryable
        # A retry waits for the chunk the first attempt is waiting for.
        tg.start_soon(read)
        await anyio.wait_all_tasks_blocked()
        resume.set()
    assert chunks == [b"Hello ", b"world", b"Hello ", b"world"]
    await anyio.wait_all_tasks_blocked()
    # No attempt has asked for the third chunk.
    assert read_from_source == [b"Hello ", b"world"]
    retrying.finish(abandoned=True)


@pytest.mark.anyio
async def test_retrying_content_earlier_attempt_ends_late() -> None:
    async def content():
        yield b"Hello "
        yield b"world!"

    retrying = RetryingRequestContent(content())
    abandoned = retrying.get()
    assert await anext(abandoned) == b"Hello "
    last = retrying.get()
    assert await anext(last) == b"Hello "
    retrying.finish(abandoned=False)
    # Only the last attempt's end stops the reading.
    await abandoned.aclose()
    assert await anext(last) == b"world!"
    await last.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["response", "error", "retries exceeded"])
async def test_content_read_stopped_when_request_ends(outcome: str) -> None:
    reading = anyio.Event()
    events: list[str] = []

    async def content():
        yield b"Hello "
        reading.set()
        try:
            await anyio.sleep_forever()
        except BaseException as e:
            events.append(type(e).__name__)
            raise

    class Transport(BaseTransport):
        def __init__(self, tg: TaskGroup) -> None:
            self.count = 0
            self._tg = tg

        async def execute(self, request: Request) -> Response:
            self.count += 1
            assert not isinstance(request.content, bytes)
            body = anyio.CancelScope()
            self._tg.start_soon(self._read_body, request.content, body)
            await reading.wait()
            body.cancel()
            match outcome:
                case "response":
                    return Response(status=400, content=b"")
                case "error":
                    msg = "Not retryable"
                    raise ValueError(msg)
                case _:
                    return Response(status=503, content=b"")

        async def _read_body(
            self, content: AsyncIterator[bytes], scope: anyio.CancelScope
        ) -> None:
            with scope:
                async for _ in content:
                    pass

    class Retry(RetryTransport):
        def should_retry_response(
            self, request: Request, response: Response | Exception
        ) -> bool:
            return not isinstance(
                response, ValueError
            ) and super().should_retry_response(request, response)

    before = asyncio.all_tasks()
    async with anyio.create_task_group() as tg:
        transport = Transport(tg)
        client = Client(
            Retry(
                transport, initial_interval=0.0, randomization_factor=0.0, max_retries=1
            )
        )
        match outcome:
            case "response":
                res = await client.put("http://localhost", content=content())
                assert res.status == 400
            case "error":
                with pytest.raises(ValueError, match="Not retryable"):
                    await client.put("http://localhost", content=content())
            case _:
                with pytest.raises(ReadError, match="Maximum retry attempts"):
                    await client.put("http://localhost", content=content())
    await anyio.wait_all_tasks_blocked()
    assert transport.count == (2 if outcome == "retries exceeded" else 1)
    assert events == ["CancelledError"]
    assert not asyncio.all_tasks() - before


@pytest.mark.anyio
async def test_retrying_content_read_by_one_task() -> None:
    # Content may hold a timeout or a task group open across a yield.
    tasks = set()
    closed_by = []

    async def content():
        nonlocal reader
        reader = asyncio.current_task()
        try:
            for chunk in (b"Hello ", b"world", b"!"):
                tasks.add(asyncio.current_task())
                yield chunk
        finally:
            closed_by.append(asyncio.current_task())

    reader = None
    retrying = RetryingRequestContent(content())

    async def read(chunks: int) -> None:
        attempt = retrying.get()
        for _ in range(chunks):
            await anext(attempt)

    # Each attempt runs in a task of its own. It gets what is buffered as one
    # chunk, then reads one more chunk from the source.
    for chunks in (1, 2, 2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(read, chunks)
    retrying.finish(abandoned=True)
    await anyio.wait_all_tasks_blocked()
    # The content is closed where it was read.
    assert tasks == {reader}
    assert closed_by == [reader]
    assert asyncio.current_task() is not reader


@pytest.mark.anyio
@pytest.mark.parametrize(
    "last_attempt", ["abandoned", "ended", "cancelled", "closed", "unread"]
)
async def test_retrying_content_finished_mid_read(last_attempt: str) -> None:
    reading = anyio.Event()
    events: list[str] = []

    async def content():
        yield b"Hello "
        reading.set()
        try:
            await anyio.sleep_forever()
        except BaseException as e:
            events.append(type(e).__name__)
            raise

    retrying = RetryingRequestContent(content())

    async def read() -> None:
        async for _ in retrying.get():
            pass

    before = asyncio.all_tasks()
    async with anyio.create_task_group() as tg:
        tg.start_soon(read)
        await reading.wait()
        # Abandoning an attempt that a retry follows leaves the read running.
        tg.cancel_scope.cancel()
    assert events == []

    match last_attempt:
        case "abandoned":
            waiting = asyncio.create_task(read())
            await anyio.wait_all_tasks_blocked()
            retrying.finish(abandoned=True)
            with anyio.fail_after(1):
                await asyncio.wait([waiting])
            assert waiting.cancelled()
        case "ended":
            attempt = retrying.get()

            async def read_attempt() -> None:
                async for _ in attempt:
                    pass

            async with anyio.create_task_group() as tg:
                tg.start_soon(read_attempt)
                await anyio.wait_all_tasks_blocked()
                tg.cancel_scope.cancel()
            retrying.finish(abandoned=False)
        case "cancelled":
            async with anyio.create_task_group() as tg:
                tg.start_soon(read)
                await anyio.wait_all_tasks_blocked()
                retrying.finish(abandoned=False)
                tg.cancel_scope.cancel()
        case "closed":
            attempt = retrying.get()
            assert await anext(attempt) == b"Hello "
            retrying.finish(abandoned=False)
            await attempt.aclose()
        case "unread":
            attempt = retrying.get()
            retrying.finish(abandoned=False)
            del attempt
    # The finalizer of an unread body calls into the loop.
    await anyio.lowlevel.checkpoint()
    await anyio.wait_all_tasks_blocked()
    assert events == ["CancelledError"]
    assert not asyncio.all_tasks() - before
    assert not retrying.retryable


@pytest.mark.anyio
@pytest.mark.parametrize("stopped", ["mid read", "between reads", "after failed read"])
async def test_retrying_content_cleanup_not_interrupted(stopped: str) -> None:
    reading = anyio.Event()
    events: list[str] = []

    async def content():
        try:
            yield b"Hello "
            if stopped == "mid read":
                reading.set()
                await anyio.sleep_forever()
            if stopped == "after failed read":
                # A chunk that cannot be buffered fails the read.
                yield cast("bytes", "world!")
            yield b"world!"
        finally:
            try:
                await anyio.sleep(0.01)
                events.append("cleaned up")
            except BaseException as e:
                events.append(type(e).__name__)
                raise

    retrying = RetryingRequestContent(content())
    attempt = retrying.get()
    assert await anext(attempt) == b"Hello "
    waiting = asyncio.create_task(anext(attempt))
    if stopped == "mid read":
        await reading.wait()
    elif stopped == "after failed read":
        with pytest.raises(TypeError):
            await waiting
    else:
        await waiting
    # The last attempt's end and its collection stop the reading as well.
    retrying.finish(abandoned=True)
    with anyio.fail_after(1):
        await asyncio.wait([waiting])
    del attempt, waiting
    await anyio.sleep(0.05)
    assert events == ["cleaned up"]


@pytest.mark.anyio
async def test_retrying_content_ignores_cancellation() -> None:
    reading = anyio.Event()

    async def content():
        yield b"Hello "
        reading.set()
        with contextlib.suppress(asyncio.CancelledError):
            await anyio.sleep_forever()
        yield b"world!"

    retrying = RetryingRequestContent(content())

    async def read() -> None:
        async for _ in retrying.get():
            pass

    before = asyncio.all_tasks()
    waiting = asyncio.create_task(read())
    await reading.wait()
    retrying.finish(abandoned=True)
    with anyio.fail_after(1):
        await asyncio.wait([waiting])
    await anyio.wait_all_tasks_blocked()
    assert not asyncio.all_tasks() - before
    assert not retrying.retryable


@pytest.mark.anyio
async def test_retrying_content_reader_cancelled() -> None:
    async def content():
        yield b"Hello "
        yield b"world!"

    retrying = RetryingRequestContent(content())
    before = asyncio.all_tasks()
    assert await anext(retrying.get()) == b"Hello "
    # As a shutdown that cancels every task does.
    for task in asyncio.all_tasks() - before:
        task.cancel()
    await anyio.wait_all_tasks_blocked()
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        await anext(retrying.get())


@pytest.mark.anyio
async def test_retrying_content_finished_before_read_starts() -> None:
    async def content():
        yield b"Hello "

    retrying = RetryingRequestContent(content())

    async def read() -> None:
        async for _ in retrying.get():
            pass

    waiting = asyncio.create_task(read())
    # Long enough for the attempt to ask for a chunk, and not for the read to
    # start.
    await anyio.lowlevel.checkpoint()
    retrying.finish(abandoned=True)
    with anyio.fail_after(1):
        await asyncio.wait([waiting])
    assert waiting.cancelled()
    assert not retrying.retryable


@pytest.mark.anyio
async def test_retrying_content_error_in_abandoned_read() -> None:
    unhandled: list[dict[str, object]] = []

    async def scenario() -> None:
        reading, resume = anyio.Event(), anyio.Event()

        async def content():
            yield b"Hello "
            reading.set()
            await resume.wait()
            msg = "boom"
            raise ValueError(msg)

        retrying = RetryingRequestContent(content())

        async def read() -> None:
            async for _ in retrying.get():
                pass

        async with anyio.create_task_group() as tg:
            tg.start_soon(read)
            await reading.wait()
            tg.cancel_scope.cancel()
        resume.set()
        await anyio.wait_all_tasks_blocked()
        assert not retrying.retryable
        with pytest.raises(RuntimeError, match="cannot be retried"):
            await anext(retrying.get())

    loop = asyncio.get_running_loop()
    handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _, context: unhandled.append(context))
    try:
        await scenario()
        # An exception that nothing retrieved is reported when its task or
        # future is collected.
        await anyio.wait_all_tasks_blocked()
        gc.collect()
        await anyio.lowlevel.checkpoint()
    finally:
        loop.set_exception_handler(handler)
    assert unhandled == []


@pytest.mark.anyio
async def test_retrying_content_chunk_not_bytes() -> None:
    async def content():
        yield b"Hello "
        yield cast("bytes", "world")
        yield b"!"

    retrying = RetryingRequestContent(content())
    with pytest.raises(TypeError):
        async for _ in retrying.get():
            pass
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        await anext(retrying.get())


@pytest.mark.anyio
async def test_retrying_content_error_after_replay() -> None:
    async def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    retrying = RetryingRequestContent(content())
    first, retry = retrying.get(), retrying.get()
    assert await anext(first) == b"Hello "
    assert await anext(retry) == b"Hello "
    with pytest.raises(ValueError, match="boom"):
        await anext(first)
    with pytest.raises(RuntimeError, match="cannot be retried"):
        await anext(retry)


@pytest.mark.anyio
async def test_retrying_content_closed_between_chunks() -> None:
    async def content():
        yield b"Hello "
        yield b"world!"

    retrying = RetryingRequestContent(content())
    attempt = retrying.get()
    assert await anext(attempt) == b"Hello "
    await attempt.aclose()
    assert retrying.retryable
    assert [chunk async for chunk in retrying.get()] == [b"Hello ", b"world!"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_count"),
    [(True, 200, 2), (RetryMode.BUFFERED, 200, 2), (False, 500, 1)],
)
async def test_buffered_retry_mode(
    app: App, mode: bool | RetryMode, expected_status: int, expected_count: int
) -> None:
    app.status = [500, 200]

    async def content():
        yield b"Hello world!"

    client = Client(ConfiguredRetryTransport(ASGITransport(app), mode))
    async with client.stream("PUT", "http://localhost", content=content()) as res:
        assert res.status == expected_status
    assert app.count == expected_count
    assert app.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_unbuffered_bytes(app: App) -> None:
    app.status = [500, 200]
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_unbuffered_bytes_io_errors(app: App) -> None:
    app.timeouts = 1
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_unbuffered_stream_connection_error(app: App) -> None:
    closed = False

    async def content():
        nonlocal closed
        try:
            yield b"Hello world!"
        finally:
            closed = True

    app.status = [500, 200]
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=content()) as res:
        assert res.status == 500
    assert app.count == 1
    assert app.read_content == b"Hello world!"
    assert closed


@pytest.mark.anyio
async def test_unread_unbuffered_stream_closed() -> None:
    class Content:
        def __init__(self) -> None:
            self.closed = False

        def __aiter__(self) -> Content:
            return self

        async def __anext__(self) -> bytes:
            return b"Hello world!"

        async def aclose(self) -> None:
            self.closed = True

    async def app(
        _scope: Scope, _receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
                "trailers": False,
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    content = Content()
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=content) as res:
        assert res.status == 200
    assert content.closed


@pytest.mark.anyio
async def test_connection_error_after_response() -> None:
    class Transport(BaseTransport):
        def __init__(self) -> None:
            self.count = 0

        async def execute(self, request: Request) -> Response:
            self.count += 1
            if self.count == 1:
                return Response(status=500, content=b"")
            if self.count == 2:
                raise ConnectionError
            return Response(status=200, content=b"")

    transport = Transport()
    client = Client(
        RetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
    )
    async with client.stream("GET", "http://localhost") as res:
        assert res.status == 200
    assert transport.count == 3


@pytest.mark.anyio
async def test_retry_timeout(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    res = await client.get("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


@pytest.mark.anyio
async def test_retries_exceeded_timeout(app: App, client: Client) -> None:
    app.status = [200, 200, 200, 200, 200, 200]
    app.timeouts = 5
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        await client.get("http://localhost")
    assert app.count == 5


@pytest.mark.anyio
async def test_no_retry_timeout_not_idempotent(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    with pytest.raises(TimeoutError):
        await client.post("http://localhost")


@pytest.mark.anyio
async def test_retry_connection_error(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.connection_errors = 1
    res = await client.post("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


@pytest.mark.anyio
async def test_retries_exceeded_connection_error(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.connection_errors = 5
    with pytest.raises(ConnectionError):
        await client.post("http://localhost")
    assert app.count == 5


@pytest.mark.anyio
async def test_retry_connection_error_content_iterator(
    app: App, client: Client
) -> None:
    read_attempts = []

    async def content():
        read_attempts.append(app.count)
        yield b"Hello "
        yield b"world!"

    app.status = [200, 200]
    app.connection_errors = 1
    res = await client.post("http://localhost", content=content())
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"
    assert read_attempts == [2]


@pytest.mark.anyio
async def test_no_retry_exception(app: App) -> None:
    class NoExceptionRetry(RetryTransport):
        def should_retry_response(
            self, request: Request, response: Response | Exception
        ) -> bool:
            if isinstance(response, Exception):
                return False
            return super().should_retry_response(request, response)

    client = Client(
        NoExceptionRetry(
            ASGITransport(app),
            initial_interval=0.01,
            randomization_factor=0.0,
            multiplier=3.0,
            max_interval=0.05,
        )
    )
    app.status = [200]
    app.timeouts = 5
    with pytest.raises(TimeoutError):
        await client.get("http://localhost")
    assert app.count == 1


@pytest.mark.anyio
async def test_retry_after_secs(app: App, client: Client) -> None:
    app.status = [429, 200]
    # Unfortunately can't avoid a slow test.
    app.retry_after = "1"
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert_duration_at_least(start, end, 1.0)


@pytest.mark.anyio
async def test_retry_after_secs_negative(app: App, client: Client) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "-1"
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.anyio
async def test_retry_after_date(app: App, client: Client) -> None:
    app.status = [429, 200]
    # Unfortunately can't avoid a very slow test. If we set for current
    # time +1s, it can be a very low delta we can't compare to the
    # standard retry. So we set +2s and check for >=1s.
    app.retry_after = formatdate(time() + 2, usegmt=True)
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert_duration_at_least(start, end, 1.0)


@pytest.mark.anyio
async def test_retry_after_date_past(app: App, client: Client) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "Wed, 21 Oct 2015 07:28:00 GMT"
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.anyio
async def test_retry_after_invalid(app: App, client: Client) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "Invalid Date String"
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)

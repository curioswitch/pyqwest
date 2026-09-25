from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from email.utils import formatdate
from threading import Event, Lock
from time import monotonic, time
from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import (
    ReadError,
    SyncClient,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    WriteError,
)
from pyqwest.middleware.retry import RetryMode, SyncRetryTransport
from pyqwest.middleware.retry._sync import RetryingRequestContent
from pyqwest.testing import WSGITransport

if TYPE_CHECKING:
    from collections.abc import Iterable

    if sys.version_info >= (3, 11):
        from wsgiref.types import InputStream as WSGIInputStream
        from wsgiref.types import StartResponse, WSGIEnvironment
    else:
        from _typeshed.wsgi import InputStream as WSGIInputStream
        from _typeshed.wsgi import StartResponse, WSGIEnvironment


class App:
    def __init__(self):
        self.status = [200]
        self.retry_after = ""
        self.read_content = b""
        self.count = 0
        self.timeouts = 0
        self.connection_errors = 0

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        self.count += 1
        if self.timeouts > 0:
            self.timeouts -= 1
            try:
                raise TimeoutError  # noqa: TRY301
            except TimeoutError:
                start_response("500 Internal Server Error", [], sys.exc_info())
            return []
        if self.connection_errors > 0:
            self.connection_errors -= 1
            try:
                raise ConnectionError  # noqa: TRY301
            except ConnectionError:
                start_response("500 Internal Server Error", [], sys.exc_info())
            return []
        request_body = cast("WSGIInputStream", environ["wsgi.input"])
        content = request_body.read()
        self.read_content = content
        headers = []
        if self.retry_after:
            headers.append(("retry-after", self.retry_after))
        try:
            status = self.status.pop(0)
        except IndexError:
            status = 500
        start_response(f"{status} {status}", headers)
        return [b""]


class ConfiguredRetryTransport(SyncRetryTransport):
    def __init__(self, transport: SyncTransport, mode: bool | RetryMode) -> None:
        self._mode = mode
        super().__init__(
            transport,
            initial_interval=0.01,
            randomization_factor=0.0,
            multiplier=3.0,
            max_interval=0.05,
        )

    def should_retry_request(self, request: SyncRequest) -> bool | RetryMode:
        return self._mode


@pytest.fixture
def app():
    return App()


@pytest.fixture
def client(app: App):
    return SyncClient(
        SyncRetryTransport(
            WSGITransport(app),
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
        SyncRetryTransport(WSGITransport(app)).should_retry_request(
            SyncRequest(method, "http://localhost")
        )
        is expected
    )


def test_success(app: App, client: SyncClient) -> None:
    res = client.get("http://localhost")
    assert res.status == 200
    assert app.count == 1
    assert app.read_content == b""


def test_one_retry(app: App, client: SyncClient) -> None:
    app.status = [500, 200]
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01)


def test_not_retryable_request(app: App, client: SyncClient) -> None:
    app.status = [500, 200]
    res = client.post("http://localhost", content=b"hello")
    assert res.status == 500
    assert app.count == 1
    assert app.read_content == b"hello"


def test_not_retryable_response(app: App, client: SyncClient) -> None:
    app.status = [404, 200]
    res = client.get("http://localhost")
    assert res.status == 404
    assert app.count == 1
    assert app.read_content == b""


def test_not_retryable_response_501(app: App, client: SyncClient) -> None:
    app.status = [501, 200]
    res = client.get("http://localhost")
    assert res.status == 501
    assert app.count == 1
    assert app.read_content == b""


def test_max_retries(app: App, client: SyncClient) -> None:
    app.status = [500, 502, 503, 504, 200]
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


def test_exceed_max_retries(app: App, client: SyncClient) -> None:
    app.status = [500, 502, 503, 504, 505, 200]
    start = monotonic()
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        client.get("http://localhost")
    end = monotonic()
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


def test_retry_fixed_content(app: App, client: SyncClient) -> None:
    content = b"Hello world!"
    app.status = [500, 200]
    res = client.put("http://localhost", content=content)
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == content


def test_retry_content_iterator(app: App, client: SyncClient) -> None:
    def content():
        yield b"Hello "
        yield b"world!"

    app.status = [500, 200]
    res = client.put("http://localhost", content=content())
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


def test_content_iterator_error_not_retried(app: App, client: SyncClient) -> None:
    def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    # The WSGI transport reports the body's error as a 500 from the app. A retry
    # would send only the content read before the error, and succeed.
    app.status = [200]
    res = client.put("http://localhost", content=content())
    assert res.status == 500
    assert app.count == 1


def test_transport_body_error_not_retried() -> None:
    class Transport(SyncTransport):
        def __init__(self) -> None:
            self.count = 0

        def execute_sync(self, request: SyncRequest) -> SyncResponse:
            self.count += 1
            try:
                for _ in request.content:
                    pass
            except ValueError as e:
                msg = "Request failed"
                raise WriteError(msg) from e
            return SyncResponse(status=200, content=b"")

    def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    transport = Transport()
    client = SyncClient(
        SyncRetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
    )
    with pytest.raises(WriteError):
        client.put("http://localhost", content=content())
    assert transport.count == 1


def test_retrying_content_error() -> None:
    def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    retrying = RetryingRequestContent(content())
    with pytest.raises(ValueError, match="boom"):
        list(retrying.get())
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        next(retrying.get())


def test_retrying_content_chunk_not_bytes() -> None:
    def content():
        yield b"Hello "
        yield cast("bytes", "world")
        yield b"!"

    retrying = RetryingRequestContent(content())
    with pytest.raises(TypeError):
        list(retrying.get())
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        next(retrying.get())


def test_retrying_content_error_after_replay() -> None:
    def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    retrying = RetryingRequestContent(content())
    first, retry = retrying.get(), retrying.get()
    assert next(first) == b"Hello "
    assert next(retry) == b"Hello "
    with pytest.raises(ValueError, match="boom"):
        next(first)
    with pytest.raises(RuntimeError, match="cannot be retried"):
        next(retry)


def test_retrying_content_read_by_abandoned_attempt() -> None:
    reading, resume, contended = Event(), Event(), Event()

    class ContendedLock:
        """Reports when a thread has to wait for the lock."""

        def __init__(self) -> None:
            self._lock = Lock()

        def __enter__(self) -> None:
            if not self._lock.acquire(blocking=False):
                contended.set()
                self._lock.acquire()

        def __exit__(self, *_: object) -> None:
            self._lock.release()

    class Content:
        """Not a generator, so a concurrent read doesn't raise."""

        def __init__(self) -> None:
            self._chunks = [b"Hello ", b"world", b"!"]

        def __iter__(self) -> Content:
            return self

        def __next__(self) -> bytes:
            if len(self._chunks) == 2:
                reading.set()
                assert resume.wait(5)
            if not self._chunks:
                raise StopIteration
            return self._chunks.pop(0)

    retrying = RetryingRequestContent(Content())
    retrying._lock = cast("Lock", ContendedLock())
    abandoned = retrying.get()
    assert next(abandoned) == b"Hello "
    # An abandoned attempt's body keeps reading on its own thread.
    with ThreadPoolExecutor(2) as pool:
        abandoned_read = pool.submit(next, abandoned)
        assert reading.wait(5)
        retry = pool.submit(lambda: b"".join(retrying.get()))
        # The retry waits for the abandoned read before it replays anything.
        assert contended.wait(5)
        resume.set()
        assert abandoned_read.result(5) == b"world"
        assert retry.result(5) == b"Hello world!"


def test_retrying_content_closed_between_chunks() -> None:
    def content():
        yield b"Hello "
        yield b"world!"

    retrying = RetryingRequestContent(content())
    attempt = retrying.get()
    assert next(attempt) == b"Hello "
    attempt.close()
    assert retrying.retryable
    assert list(retrying.get()) == [b"Hello ", b"world!"]


@pytest.mark.parametrize(
    ("mode", "expected_status", "expected_count"),
    [(True, 200, 2), (RetryMode.BUFFERED, 200, 2), (False, 500, 1)],
)
def test_buffered_retry_mode(
    app: App, mode: bool | RetryMode, expected_status: int, expected_count: int
) -> None:
    app.status = [500, 200]

    def content():
        yield b"Hello world!"

    client = SyncClient(ConfiguredRetryTransport(WSGITransport(app), mode))
    with client.stream("PUT", "http://localhost", content=content()) as res:
        assert res.status == expected_status
    assert app.count == expected_count
    assert app.read_content == b"Hello world!"


def test_unbuffered_bytes(app: App) -> None:
    app.status = [500, 200]
    client = SyncClient(
        ConfiguredRetryTransport(WSGITransport(app), RetryMode.UNBUFFERED)
    )
    with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


def test_unbuffered_bytes_io_errors(app: App) -> None:
    app.timeouts = 1
    client = SyncClient(
        ConfiguredRetryTransport(WSGITransport(app), RetryMode.UNBUFFERED)
    )
    with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


def test_unbuffered_stream_connection_error(app: App) -> None:
    closed = False

    def content():
        nonlocal closed
        try:
            yield b"Hello world!"
        finally:
            closed = True

    app.status = [500, 200]
    client = SyncClient(
        ConfiguredRetryTransport(WSGITransport(app), RetryMode.UNBUFFERED)
    )
    with client.stream("PUT", "http://localhost", content=content()) as res:
        assert res.status == 500
    assert app.count == 1
    assert app.read_content == b"Hello world!"
    assert closed


def test_unread_unbuffered_stream_closed() -> None:
    class Content:
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self) -> Content:
            return self

        def __next__(self) -> bytes:
            return b"Hello world!"

        def close(self) -> None:
            self.closed = True

    def app(
        _environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        start_response("200 OK", [])
        return []

    content = Content()
    client = SyncClient(
        ConfiguredRetryTransport(WSGITransport(app), RetryMode.UNBUFFERED)
    )
    with client.stream("PUT", "http://localhost", content=content) as res:
        assert res.status == 200
    assert content.closed


def test_connection_error_after_response() -> None:
    class Transport(SyncTransport):
        def __init__(self) -> None:
            self.count = 0

        def execute_sync(self, request: SyncRequest) -> SyncResponse:
            self.count += 1
            if self.count == 1:
                return SyncResponse(status=500, content=b"")
            if self.count == 2:
                raise ConnectionError
            return SyncResponse(status=200, content=b"")

    transport = Transport()
    client = SyncClient(
        SyncRetryTransport(transport, initial_interval=0.0, randomization_factor=0.0)
    )
    with client.stream("GET", "http://localhost") as res:
        assert res.status == 200
    assert transport.count == 3


def test_retry_timeout(app: App, client: SyncClient) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    res = client.get("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


def test_retries_exceeded_timeout(app: App, client: SyncClient) -> None:
    app.status = [200, 200, 200, 200, 200, 200]
    app.timeouts = 5
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        client.get("http://localhost")
    assert app.count == 5


def test_no_retry_timeout_not_idempotent(app: App, client: SyncClient) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    with pytest.raises(TimeoutError):
        client.post("http://localhost")


def test_retry_connection_error(app: App, client: SyncClient) -> None:
    app.status = [200, 200]
    app.connection_errors = 1
    res = client.post("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


def test_retries_exceeded_connection_error(app: App, client: SyncClient) -> None:
    app.status = [200, 200, 200, 200, 200, 200]
    app.connection_errors = 5
    with pytest.raises(ConnectionError):
        client.get("http://localhost")
    assert app.count == 5


def test_retry_connection_error_content_iterator(app: App, client: SyncClient) -> None:
    def content():
        yield b"Hello "
        yield b"world!"

    app.status = [200, 200]
    app.connection_errors = 1
    res = client.post("http://localhost", content=content())
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


def test_no_retry_exception(app: App) -> None:
    class NoExceptionRetry(SyncRetryTransport):
        def should_retry_response(
            self, request: SyncRequest, response: SyncResponse | Exception
        ) -> bool:
            if isinstance(response, Exception):
                return False
            return super().should_retry_response(request, response)

    client = SyncClient(
        NoExceptionRetry(
            WSGITransport(app),
            initial_interval=0.01,
            randomization_factor=0.0,
            multiplier=3.0,
            max_interval=0.05,
        )
    )
    app.status = [200]
    app.timeouts = 5
    with pytest.raises(TimeoutError):
        client.get("http://localhost")
    assert app.count == 1


def test_retry_after_secs(app: App, client: SyncClient) -> None:
    app.status = [429, 200]
    # Unfortunately can't avoid a slow test.
    app.retry_after = "1"
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert_duration_at_least(start, end, 1.0)


def test_retry_after_secs_negative(app: App, client: SyncClient) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "-1"
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


def test_retry_after_date(app: App, client: SyncClient) -> None:
    app.status = [429, 200]
    # Unfortunately can't avoid a very slow test. If we set for current
    # time +1s, it can be a very low delta we can't compare to the
    # standard retry. So we set +2s and check for >=1s.
    app.retry_after = formatdate(time() + 2, usegmt=True)
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert_duration_at_least(start, end, 1.0)


def test_retry_after_date_past(app: App, client: SyncClient) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "Wed, 21 Oct 2015 07:28:00 GMT"
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


def test_retry_after_invalid(app: App, client: SyncClient) -> None:
    app.status = [429, 429, 429, 429, 200]
    app.retry_after = "Invalid Date String"
    start = monotonic()
    res = client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)

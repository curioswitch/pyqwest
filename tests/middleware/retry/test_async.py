from __future__ import annotations

import sys
from email.utils import formatdate
from time import monotonic, time
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client, ReadError, Request, Response
from pyqwest import Transport as BaseTransport
from pyqwest.middleware.retry import RetryMode, RetryTransport
from pyqwest.testing import ASGITransport

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope


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


@pytest.mark.asyncio
async def test_success(app: App, client: Client) -> None:
    res = await client.get("http://localhost")
    assert res.status == 200
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.asyncio
async def test_one_retry(app: App, client: Client) -> None:
    app.status = [500, 200]
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01)


@pytest.mark.asyncio
async def test_not_retryable_request(app: App, client: Client) -> None:
    app.status = [500, 200]
    res = await client.post("http://localhost", content=b"hello")
    assert res.status == 500
    assert app.count == 1
    assert app.read_content == b"hello"


@pytest.mark.asyncio
async def test_not_retryable_response(app: App, client: Client) -> None:
    app.status = [404, 200]
    res = await client.get("http://localhost")
    assert res.status == 404
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.asyncio
async def test_not_retryable_response_501(app: App, client: Client) -> None:
    app.status = [501, 200]
    res = await client.get("http://localhost")
    assert res.status == 501
    assert app.count == 1
    assert app.read_content == b""


@pytest.mark.asyncio
async def test_max_retries(app: App, client: Client) -> None:
    app.status = [500, 502, 503, 504, 200]
    start = monotonic()
    res = await client.get("http://localhost")
    end = monotonic()
    assert res.status == 200
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.asyncio
async def test_exceed_max_retries(app: App, client: Client) -> None:
    app.status = [500, 502, 503, 504, 505, 200]
    start = monotonic()
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        await client.get("http://localhost")
    end = monotonic()
    assert app.count == 5
    assert app.read_content == b""
    assert_duration_at_least(start, end, 0.01 + 0.03 + 0.05 + 0.05)


@pytest.mark.asyncio
async def test_retry_fixed_content(app: App, client: Client) -> None:
    content = b"Hello world!"
    app.status = [500, 200]
    res = await client.put("http://localhost", content=content)
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == content


@pytest.mark.asyncio
async def test_retry_content_iterator(app: App, client: Client) -> None:
    async def content():
        yield b"Hello "
        yield b"world!"

    app.status = [500, 200]
    res = await client.put("http://localhost", content=content())
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_unbuffered_bytes(app: App) -> None:
    app.status = [500, 200]
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.asyncio
async def test_unbuffered_bytes_io_errors(app: App) -> None:
    app.timeouts = 1
    client = Client(ConfiguredRetryTransport(ASGITransport(app), RetryMode.UNBUFFERED))
    async with client.stream("PUT", "http://localhost", content=b"Hello world!") as res:
        assert res.status == 200
    assert app.count == 2
    assert app.read_content == b"Hello world!"


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_retry_timeout(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    res = await client.get("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


@pytest.mark.asyncio
async def test_retries_exceeded_timeout(app: App, client: Client) -> None:
    app.status = [200, 200, 200, 200, 200, 200]
    app.timeouts = 5
    with pytest.raises(ReadError, match="Maximum retry attempts exceeded: 4"):
        await client.get("http://localhost")
    assert app.count == 5


@pytest.mark.asyncio
async def test_no_retry_timeout_not_idempotent(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.timeouts = 1
    with pytest.raises(TimeoutError):
        await client.post("http://localhost")


@pytest.mark.asyncio
async def test_retry_connection_error(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.connection_errors = 1
    res = await client.post("http://localhost")
    assert res.status == 200
    assert app.count == 2
    assert app.read_content == b""


@pytest.mark.asyncio
async def test_retries_exceeded_connection_error(app: App, client: Client) -> None:
    app.status = [200, 200]
    app.connection_errors = 5
    with pytest.raises(ConnectionError):
        await client.post("http://localhost")
    assert app.count == 5


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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

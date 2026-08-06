from __future__ import annotations

import asyncio
import logging
import socket
import threading
from typing import TYPE_CHECKING, cast

import httpx
import pytest
from h2.errors import ErrorCodes

from pyqwest import (
    ConnectTimeout,
    Headers,
    HTTPTransport,
    ReadError,
    RemoteProtocolError,
    Request,
    Response,
    StreamError,
    StreamErrorCode,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
    Transport,
    WriteError,
)
from pyqwest.httpx import AsyncPyqwestTransport, PyqwestTransport
from pyqwest.httpx._transport import convert_headers
from pyqwest.testing import ASGITransport, WSGITransport

from ._util import (
    BAD_CHUNKED_FRAMING,
    GARBAGE_RESPONSE,
    MALFORMED_STATUS_LINE,
    NO_RESPONSE,
    TRUNCATED_CHUNKED_RESPONSE,
    TRUNCATED_RESPONSE,
    raw_server,
)

if TYPE_CHECKING:
    import sys
    from collections.abc import AsyncIterator, Iterable, Iterator

    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

    if sys.version_info >= (3, 11):
        from wsgiref.types import StartResponse, WSGIEnvironment
    else:
        from _typeshed.wsgi import StartResponse, WSGIEnvironment


async def echo_app(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    if scope["type"] != "http":
        return
    content = b""
    while True:
        message = await receive()
        if message["type"] == "http.request":
            content += message.get("body", b"")
            if not message.get("more_body", False):
                break
    headers = [(b"x-request-method", scope["method"].encode("utf-8"))]
    for name, value in scope["headers"]:
        if name == b"content-type":
            headers.append((b"x-request-content-type", value))
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": headers,
            "trailers": False,
        }
    )
    await send({"type": "http.response.body", "body": content, "more_body": False})


def sync_echo_app(
    environ: WSGIEnvironment, start_response: StartResponse
) -> Iterable[bytes]:
    content = environ["wsgi.input"].read()
    headers = [("x-request-method", environ["REQUEST_METHOD"])]
    if content_type := environ.get("CONTENT_TYPE"):
        headers.append(("x-request-content-type", content_type))
    start_response("200 OK", headers)
    return [content]


def assert_multipart_echo(res: httpx.Response) -> None:
    assert res.status_code == 200
    content_type = res.headers["x-request-content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    boundary = content_type.removeprefix("multipart/form-data; boundary=")
    assert res.content.startswith(f"--{boundary}\r\n".encode())
    assert res.content.rstrip(b"\r\n").endswith(f"--{boundary}--".encode())
    assert b'name="field"' in res.content
    assert b"hello" in res.content
    assert b'filename="f.bin"' in res.content
    assert b"file bytes" in res.content


@pytest.mark.asyncio
async def test_async_get() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.get("http://localhost/")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "GET"
    assert res.content == b""


@pytest.mark.asyncio
async def test_async_post_content() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "POST"
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_post_content_iterator() -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"Hello "
        yield b"world!"

    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post("http://localhost/", content=content())
    assert res.status_code == 200
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_post_multipart() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post(
            "http://localhost/",
            data={"field": "hello"},
            files={"file": ("f.bin", b"file bytes", "application/octet-stream")},
        )
    assert_multipart_echo(res)


@pytest.mark.asyncio
async def test_async_response_stream() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with (
        httpx.AsyncClient(transport=transport) as client,
        client.stream("POST", "http://localhost/", content=b"Hello world!") as res,
    ):
        assert res.status_code == 200
        content = b""
        async for chunk in res.aiter_raw():
            content += chunk
    assert content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_no_timeout() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport, timeout=None) as client:  # noqa: S113
        res = await client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_timeout_headers() -> None:
    release = asyncio.Event()

    async def app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await release.wait()
        await echo_app(scope, receive, send)

    transport = AsyncPyqwestTransport(ASGITransport(app))
    try:
        async with httpx.AsyncClient(transport=transport, timeout=0.1) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.get("http://localhost/")
    finally:
        release.set()
        # Let the application task finish before the event loop closes.
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_async_timeout_response_content() -> None:
    release = asyncio.Event()

    async def app(
        scope: Scope, _receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
                "trailers": False,
            }
        )
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        await release.wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    transport = AsyncPyqwestTransport(ASGITransport(app))
    try:
        async with (
            httpx.AsyncClient(transport=transport, timeout=0.2) as client,
            client.stream("GET", "http://localhost/") as res,
        ):
            assert res.status_code == 200
            content = b""
            with pytest.raises(httpx.ReadTimeout):
                async for chunk in res.aiter_raw():
                    content += chunk
            assert content == b"partial"
    finally:
        release.set()
        await asyncio.sleep(0.01)


def test_sync_get() -> None:
    transport = PyqwestTransport(WSGITransport(sync_echo_app))
    with httpx.Client(transport=transport) as client:
        res = client.get("http://localhost/")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "GET"
    assert res.content == b""


def test_sync_post_content() -> None:
    transport = PyqwestTransport(WSGITransport(sync_echo_app))
    with httpx.Client(transport=transport) as client:
        res = client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "POST"
    assert res.content == b"Hello world!"


def test_sync_post_multipart() -> None:
    transport = PyqwestTransport(WSGITransport(sync_echo_app))
    with httpx.Client(transport=transport) as client:
        res = client.post(
            "http://localhost/",
            data={"field": "hello"},
            files={"file": ("f.bin", b"file bytes", "application/octet-stream")},
        )
    assert_multipart_echo(res)


def test_sync_timeout() -> None:
    release = threading.Event()

    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        release.wait(5)
        return sync_echo_app(environ, start_response)

    transport = PyqwestTransport(WSGITransport(app))
    try:
        with (
            httpx.Client(transport=transport, timeout=0.1) as client,
            pytest.raises(httpx.ReadTimeout),
        ):
            client.get("http://localhost/")
    finally:
        release.set()


def test_convert_headers_strips_default_host() -> None:
    request = httpx.Request("GET", "https://example.com:8443/path")
    assert request.headers["host"] == "example.com:8443"
    assert "host" not in convert_headers(request)


def test_convert_headers_strips_default_host_case_insensitively() -> None:
    request = httpx.Request(
        "GET", "https://example.com/", headers={"Host": "EXAMPLE.com"}
    )
    assert "host" not in convert_headers(request)


def test_convert_headers_keeps_custom_host() -> None:
    request = httpx.Request(
        "GET", "https://example.com/", headers={"host": "other.example.com"}
    )
    assert convert_headers(request)["host"] == "other.example.com"


def test_convert_headers_strips_transport_headers() -> None:
    request = httpx.Request(
        "GET",
        "https://example.com/",
        headers={"connection": "close", "x-custom": "kept"},
    )
    headers = convert_headers(request)
    assert "connection" not in headers
    assert headers["x-custom"] == "kept"


class RecordingTransport(Transport):
    def __init__(self) -> None:
        self.headers: list[Headers] = []

    async def execute(self, request: Request) -> Response:
        self.headers.append(request.headers)
        return Response(status=200)

    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        self.headers.append(request.headers)
        return SyncResponse(status=200)


@pytest.mark.asyncio
async def test_async_host_header() -> None:
    recording = RecordingTransport()
    transport = AsyncPyqwestTransport(recording)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.get("http://localhost/")
        assert res.status_code == 200
        res = await client.get("http://localhost/", headers={"host": "LOCALHOST"})
        assert res.status_code == 200
        res = await client.get("http://localhost/", headers={"host": "example.com"})
        assert res.status_code == 200

    assert "host" not in recording.headers[0]
    assert "host" not in recording.headers[1]
    assert recording.headers[2]["host"] == "example.com"


def test_sync_host_header() -> None:
    recording = RecordingTransport()
    transport = PyqwestTransport(recording)
    with httpx.Client(transport=transport) as client:
        res = client.get("http://localhost/")
        assert res.status_code == 200
        res = client.get("http://localhost/", headers={"host": "LOCALHOST"})
        assert res.status_code == 200
        res = client.get("http://localhost/", headers={"host": "example.com"})
        assert res.status_code == 200

    assert "host" not in recording.headers[0]
    assert "host" not in recording.headers[1]
    assert recording.headers[2]["host"] == "example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_async_redirects_handled_by_pyqwest(url: str) -> None:
    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            res = await client.get(f"{url}/redirect")
            assert res.status_code == 200
            assert res.history == []


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_sync_redirects_handled_by_pyqwest(url: str) -> None:
    def run() -> None:
        with SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with httpx.Client(transport=transport) as client:
                res = client.get(f"{url}/redirect")
                assert res.status_code == 200
                assert res.history == []

    await asyncio.to_thread(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_async_redirects_handled_by_httpx(url: str) -> None:
    async with HTTPTransport(follow_redirects=False) as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            res = await client.get(f"{url}/redirect")
            assert res.status_code == 302
            assert res.headers["location"] == "/echo"
            assert res.history == []

            res = await client.get(f"{url}/redirect?n=3", follow_redirects=True)
            assert res.status_code == 200
            assert len(res.history) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_sync_redirects_handled_by_httpx(url: str) -> None:
    def run() -> None:
        with SyncHTTPTransport(follow_redirects=False) as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with httpx.Client(transport=transport) as client:
                res = client.get(f"{url}/redirect")
                assert res.status_code == 302
                assert res.headers["location"] == "/echo"
                assert res.history == []

                res = client.get(f"{url}/redirect?n=3", follow_redirects=True)
                assert res.status_code == 200
                assert len(res.history) == 3

    await asyncio.to_thread(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_async_redirects_handled_by_pyqwest_exceed_max(url: str) -> None:
    async with HTTPTransport(max_redirects=1) as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.TooManyRedirects):
                await client.get(f"{url}/redirect?n=5")


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_sync_redirects_handled_by_pyqwest_exceed_max(url: str) -> None:
    def run() -> None:
        with SyncHTTPTransport(max_redirects=1) as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with (
                httpx.Client(transport=transport) as client,
                pytest.raises(httpx.TooManyRedirects),
            ):
                client.get(f"{url}/redirect?n=5")

    await asyncio.to_thread(run)


def refused_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}/"


# A non-routable address, so connection attempts hang until timeout.
BLACKHOLE_HOST = "10.255.255.1"
BLACKHOLE_URL = f"http://{BLACKHOLE_HOST}:81/"


def require_blackhole() -> None:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        try:
            probe.connect((BLACKHOLE_HOST, 81))
        except TimeoutError:
            return
        except OSError:
            pass
    pytest.skip(f"network does not blackhole {BLACKHOLE_HOST}")


@pytest.mark.asyncio
async def test_async_connect_error() -> None:
    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ConnectError) as excinfo:
                await client.get(refused_url())
    assert isinstance(excinfo.value.__cause__, ConnectionError)


@pytest.mark.asyncio
async def test_async_connect_timeout() -> None:
    require_blackhole()
    async with HTTPTransport(connect_timeout=0.2) as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ConnectTimeout) as excinfo:
                await client.get(BLACKHOLE_URL)
    assert isinstance(excinfo.value.__cause__, ConnectTimeout)


def test_sync_connect_error() -> None:
    with SyncHTTPTransport() as pyqwest_transport:
        transport = PyqwestTransport(pyqwest_transport)
        with (
            httpx.Client(transport=transport) as client,
            pytest.raises(httpx.ConnectError) as excinfo,
        ):
            client.get(refused_url())
    assert isinstance(excinfo.value.__cause__, ConnectionError)


def test_sync_connect_timeout() -> None:
    require_blackhole()
    with SyncHTTPTransport(connect_timeout=0.2) as pyqwest_transport:
        transport = PyqwestTransport(pyqwest_transport)
        with (
            httpx.Client(transport=transport) as client,
            pytest.raises(httpx.ConnectTimeout) as excinfo,
        ):
            client.get(BLACKHOLE_URL)
    assert isinstance(excinfo.value.__cause__, ConnectTimeout)


def access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "pyqwest.access"]


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_async_access_log(url: str, caplog: pytest.LogCaptureFixture) -> None:
    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            with caplog.at_level(logging.DEBUG, logger="pyqwest.access"):
                res = await client.get(f"{url}/echo")
    assert res.status_code == 200

    records = access_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == f'HTTP Request: GET {url}/echo "HTTP/1.1 200 OK"'


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_sync_access_log(url: str, caplog: pytest.LogCaptureFixture) -> None:
    def run() -> httpx.Response:
        with SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with httpx.Client(transport=transport) as client:
                return client.get(f"{url}/echo")

    with caplog.at_level(logging.DEBUG, logger="pyqwest.access"):
        res = await asyncio.to_thread(run)
    assert res.status_code == 200

    records = access_records(caplog)
    assert len(records) == 1
    assert records[0].getMessage() == f'HTTP Request: GET {url}/echo "HTTP/1.1 200 OK"'


# Each of these is a way for the peer to break HTTP framing. httpx reports all of
# them as RemoteProtocolError, so the transports must too.
PROTOCOL_FAULTS = [
    pytest.param(NO_RESPONSE, id="no-response"),
    pytest.param(MALFORMED_STATUS_LINE, id="malformed-status-line"),
    pytest.param(GARBAGE_RESPONSE, id="garbage-response"),
    pytest.param(BAD_CHUNKED_FRAMING, id="bad-chunked-framing"),
    pytest.param(TRUNCATED_RESPONSE, id="truncated-body"),
    pytest.param(TRUNCATED_CHUNKED_RESPONSE, id="truncated-chunked-body"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", PROTOCOL_FAULTS)
async def test_async_protocol_error(response: bytes) -> None:
    with raw_server(response) as url:
        async with HTTPTransport() as pyqwest_transport:
            transport = AsyncPyqwestTransport(pyqwest_transport)
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(httpx.RemoteProtocolError) as excinfo:
                    await client.get(url)
    assert isinstance(excinfo.value.__cause__, RemoteProtocolError)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", PROTOCOL_FAULTS)
async def test_sync_protocol_error(response: bytes) -> None:
    def run() -> None:
        with raw_server(response) as url, SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with (
                httpx.Client(transport=transport) as client,
                pytest.raises(httpx.RemoteProtocolError) as excinfo,
            ):
                client.get(url)
        assert isinstance(excinfo.value.__cause__, RemoteProtocolError)

    await asyncio.to_thread(run)


class StreamErrorTransport(Transport):
    """Fails every request with an HTTP/2 stream error."""

    async def execute(self, request: Request) -> Response:
        raise StreamError("boom", StreamErrorCode.REFUSED_STREAM)

    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        raise StreamError("boom", StreamErrorCode.REFUSED_STREAM)


@pytest.mark.asyncio
async def test_async_stream_error_keeps_reset_message() -> None:
    # StreamError subclasses RemoteProtocolError, so it has to stay matched first
    # to keep the stream reset detail rather than falling back to the plain
    # protocol error message.
    transport = AsyncPyqwestTransport(StreamErrorTransport())
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.RemoteProtocolError) as excinfo:
            await client.get("http://localhost/")
    # The plain protocol error message would just be the "boom" above. Compare
    # against the rendered enum rather than a literal, because IntEnum.__str__
    # gives the name rather than the number before Python 3.11.
    assert "StreamReset" in str(excinfo.value)
    assert f"error_code:{ErrorCodes.REFUSED_STREAM!s}" in str(excinfo.value)


def test_sync_stream_error_keeps_reset_message() -> None:
    transport = PyqwestTransport(StreamErrorTransport())
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(httpx.RemoteProtocolError) as excinfo,
    ):
        client.get("http://localhost/")
    # The plain protocol error message would just be the "boom" above. Compare
    # against the rendered enum rather than a literal, because IntEnum.__str__
    # gives the name rather than the number before Python 3.11.
    assert "StreamReset" in str(excinfo.value)
    assert f"error_code:{ErrorCodes.REFUSED_STREAM!s}" in str(excinfo.value)


@pytest.mark.asyncio
async def test_async_response_reset() -> None:
    # A reset breaks the connection rather than the protocol, so unlike the
    # truncations above it stays a read error, matching httpx.
    with raw_server(TRUNCATED_RESPONSE, reset=True) as url:
        async with HTTPTransport() as pyqwest_transport:
            transport = AsyncPyqwestTransport(pyqwest_transport)
            async with httpx.AsyncClient(transport=transport) as client:
                # Usually a read error but timing can make it a write error
                # especially on Windows.
                with pytest.raises((httpx.ReadError, httpx.WriteError)) as excinfo:
                    await client.get(url)
    assert isinstance(excinfo.value.__cause__, (ReadError, WriteError))


@pytest.mark.asyncio
async def test_sync_response_reset() -> None:
    def run() -> None:
        with (
            raw_server(TRUNCATED_RESPONSE, reset=True) as url,
            SyncHTTPTransport() as pyqwest_transport,
        ):
            transport = PyqwestTransport(pyqwest_transport)
            with (
                httpx.Client(transport=transport) as client,
                # Usually a read error but timing can make it a write error
                # especially on Windows.
                pytest.raises((httpx.ReadError, httpx.WriteError)) as excinfo,
            ):
                client.get(url)
        assert isinstance(excinfo.value.__cause__, (ReadError, WriteError))

    await asyncio.to_thread(run)


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_async_request_body_error(url: str) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield cast("bytes", 10)

    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            # Can surface on either side depending on timing.
            with pytest.raises((httpx.WriteError, httpx.ReadError)) as excinfo:
                await client.post(f"{url}/echo", content=content())
    assert isinstance(excinfo.value.__cause__, (WriteError, ReadError))


@pytest.mark.asyncio
@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1"], indirect=True)
async def test_sync_request_body_error(url: str) -> None:
    def content() -> Iterator[bytes]:
        yield cast("bytes", 10)

    def run() -> None:
        with SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with (
                httpx.Client(transport=transport) as client,
                pytest.raises((httpx.WriteError, httpx.ReadError)) as excinfo,
            ):
                client.post(f"{url}/echo", content=content())
        assert isinstance(excinfo.value.__cause__, (WriteError, ReadError))

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_async_unsupported_scheme() -> None:
    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.UnsupportedProtocol):
                await client.get("ftp://127.0.0.1:1/")


@pytest.mark.asyncio
async def test_sync_unsupported_scheme() -> None:
    def run() -> None:
        with SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with (
                httpx.Client(transport=transport) as client,
                pytest.raises(httpx.UnsupportedProtocol),
            ):
                client.get("ftp://127.0.0.1:1/")

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_async_malformed_request() -> None:
    url = refused_url()
    async with HTTPTransport() as pyqwest_transport:
        transport = AsyncPyqwestTransport(pyqwest_transport)
        async with httpx.AsyncClient(transport=transport) as client:
            # Rejected while building the request, so nothing is ever sent and
            # the refused port is never connected to.
            with pytest.raises(httpx.LocalProtocolError) as excinfo:
                await client.get(url, headers={"bad name": "value"})
            assert isinstance(excinfo.value.__cause__, ValueError)
            with pytest.raises(httpx.LocalProtocolError):
                await client.request("BAD METHOD", url)


@pytest.mark.asyncio
async def test_sync_malformed_request() -> None:
    def run() -> None:
        url = refused_url()
        with SyncHTTPTransport() as pyqwest_transport:
            transport = PyqwestTransport(pyqwest_transport)
            with httpx.Client(transport=transport) as client:
                with pytest.raises(httpx.LocalProtocolError) as excinfo:
                    client.get(url, headers={"bad name": "value"})
                assert isinstance(excinfo.value.__cause__, ValueError)
                with pytest.raises(httpx.LocalProtocolError):
                    client.request("BAD METHOD", url)

    await asyncio.to_thread(run)

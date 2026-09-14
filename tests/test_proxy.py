from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio import to_thread
from anyio.abc import SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream

from pyqwest import Client, HTTPTransport, Proxy, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.abc import SocketStream

# The target host does not resolve, so a successful response can only
# have been served by the proxy.
TARGET_URL = "http://pyqwest.invalid/echo"


@dataclass
class RecordingProxy:
    host: str
    port: int
    requests: list[bytes] = field(default_factory=list)

    def url(self, credentials: str = "") -> str:
        return f"http://{credentials}{self.host}:{self.port}"

    def request_line(self) -> bytes:
        return self.requests[0].split(b"\r\n")[0]

    def request_headers(self) -> dict[bytes, bytes]:
        headers = {}
        for line in self.requests[0].split(b"\r\n")[1:]:
            if not line:
                continue
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()
        return headers


@pytest.fixture
async def proxy() -> AsyncIterator[RecordingProxy]:
    recorded: list[bytes] = []

    async def handle(stream: SocketStream) -> None:
        async with stream:
            buffered = BufferedByteReceiveStream(stream)
            head = await buffered.receive_until(b"\r\n\r\n", 65536)
            recorded.append(head + b"\r\n\r\n")
            await stream.send(
                b"HTTP/1.1 200 OK\r\ncontent-length: 5\r\nconnection: close\r\n\r\nproxy"
            )

    listener = await anyio.create_tcp_listener(local_host="127.0.0.1")
    async with listener, anyio.create_task_group() as tg:
        tg.start_soon(listener.serve, handle)
        port = listener.extra(SocketAttribute.local_port)  # noqa: S610  # not Django
        yield RecordingProxy(host="127.0.0.1", port=port, requests=recorded)
        tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_proxy(proxy: RecordingProxy) -> None:
    async with HTTPTransport(proxy=proxy.url(), timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.anyio
async def test_proxy_sync(proxy: RecordingProxy) -> None:
    with SyncHTTPTransport(proxy=proxy.url(), timeout=10) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.anyio
async def test_proxy_credentials(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=proxy.url(credentials="user:pass@"), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    # base64 of "user:pass"
    assert proxy.request_headers()[b"proxy-authorization"] == b"Basic dXNlcjpwYXNz"


@pytest.mark.anyio
async def test_proxy_credentials_sync(proxy: RecordingProxy) -> None:
    with SyncHTTPTransport(
        proxy=proxy.url(credentials="user:pass@"), timeout=10
    ) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    # base64 of "user:pass"
    assert proxy.request_headers()[b"proxy-authorization"] == b"Basic dXNlcjpwYXNz"


def test_proxy_invalid_url() -> None:
    with pytest.raises(ValueError, match="Failed to parse proxy URL"):
        HTTPTransport(proxy="not a url")


def test_proxy_invalid_url_sync() -> None:
    with pytest.raises(ValueError, match="Failed to parse proxy URL"):
        SyncHTTPTransport(proxy="not a url")


@pytest.mark.anyio
async def test_proxy_object(proxy: RecordingProxy) -> None:
    async with HTTPTransport(proxy=Proxy(proxy.url()), timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.anyio
async def test_proxy_object_sync(proxy: RecordingProxy) -> None:
    with SyncHTTPTransport(proxy=Proxy(proxy.url()), timeout=10) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.anyio
async def test_proxy_object_auth(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), auth=("user", "pass")), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    # base64 of "user:pass"
    assert proxy.request_headers()[b"proxy-authorization"] == b"Basic dXNlcjpwYXNz"


@pytest.mark.anyio
async def test_proxy_object_headers(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), headers={"x-tenant": "my-tenant"}), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert proxy.request_headers()[b"x-tenant"] == b"my-tenant"


@pytest.mark.anyio
async def test_proxy_object_no_proxy_match(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), no_proxy="pyqwest.invalid"), timeout=10
    ) as transport:
        # The target host is excluded from proxying and does not resolve.
        with pytest.raises(ConnectionError):
            await Client(transport).get(TARGET_URL)
    assert not proxy.requests


@pytest.mark.anyio
async def test_proxy_object_no_proxy_no_match(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), no_proxy="other.invalid"), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"


@pytest.mark.anyio
async def test_proxy_object_scheme_http(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), scheme="http"), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"


@pytest.mark.anyio
async def test_proxy_object_scheme_https(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=Proxy(proxy.url(), scheme="https"), timeout=10
    ) as transport:
        # Only https requests are proxied, so the http request connects
        # directly to the target host, which does not resolve.
        with pytest.raises(ConnectionError):
            await Client(transport).get(TARGET_URL)
    assert not proxy.requests


@pytest.mark.anyio
async def test_proxy_sequence(proxy: RecordingProxy) -> None:
    proxies = [
        Proxy("http://other.invalid:8030", scheme="https"),
        Proxy(proxy.url(), scheme="http"),
    ]
    async with HTTPTransport(proxy=proxies, timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.anyio
async def test_proxy_sequence_sync(proxy: RecordingProxy) -> None:
    proxies = [
        Proxy("http://other.invalid:8030", scheme="https"),
        Proxy(proxy.url(), scheme="http"),
    ]
    with SyncHTTPTransport(proxy=proxies, timeout=10) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"


@pytest.mark.anyio
async def test_proxy_sequence_url_strings(proxy: RecordingProxy) -> None:
    async with HTTPTransport(proxy=[proxy.url()], timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"


def test_proxy_object_invalid_url() -> None:
    with pytest.raises(ValueError, match="Failed to parse proxy URL"):
        Proxy("not a url")


def test_proxy_object_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="Invalid proxy scheme"):
        Proxy("http://localhost:8030", scheme="socks5")  # ty: ignore[invalid-argument-type]


def test_proxy_invalid_type() -> None:
    with pytest.raises(TypeError):
        HTTPTransport(proxy=1)  # ty: ignore[invalid-argument-type]


def test_proxy_invalid_item_type() -> None:
    with pytest.raises(TypeError, match="proxy must be"):
        HTTPTransport(proxy=[1])  # ty: ignore[invalid-argument-type]


def test_proxy_repr_masks_password() -> None:
    rendered = repr(Proxy("http://user:pass@localhost:8030"))
    assert "pass" not in rendered
    assert rendered == 'Proxy(url="http://user:********@localhost:8030/")'

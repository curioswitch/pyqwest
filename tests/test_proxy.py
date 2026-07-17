from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from pyqwest import Client, HTTPTransport, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


@pytest_asyncio.fixture
async def proxy() -> AsyncIterator[RecordingProxy]:
    recorded: list[bytes] = []

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        recorded.append(await reader.readuntil(b"\r\n\r\n"))
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"content-length: 5\r\n"
            b"connection: close\r\n"
            b"\r\n"
            b"proxy"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield RecordingProxy(host="127.0.0.1", port=port, requests=recorded)
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_proxy(proxy: RecordingProxy) -> None:
    async with HTTPTransport(proxy=proxy.url(), timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.asyncio
async def test_proxy_sync(proxy: RecordingProxy) -> None:
    with SyncHTTPTransport(proxy=proxy.url(), timeout=10) as transport:
        res = await asyncio.to_thread(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    assert res.content == b"proxy"
    assert proxy.request_line() == b"GET http://pyqwest.invalid/echo HTTP/1.1"


@pytest.mark.asyncio
async def test_proxy_credentials(proxy: RecordingProxy) -> None:
    async with HTTPTransport(
        proxy=proxy.url(credentials="user:pass@"), timeout=10
    ) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    # base64 of "user:pass"
    assert proxy.request_headers()[b"proxy-authorization"] == b"Basic dXNlcjpwYXNz"


@pytest.mark.asyncio
async def test_proxy_credentials_sync(proxy: RecordingProxy) -> None:
    with SyncHTTPTransport(
        proxy=proxy.url(credentials="user:pass@"), timeout=10
    ) as transport:
        res = await asyncio.to_thread(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    # base64 of "user:pass"
    assert proxy.request_headers()[b"proxy-authorization"] == b"Basic dXNlcjpwYXNz"


def test_proxy_invalid_url() -> None:
    with pytest.raises(ValueError, match="Failed to parse proxy URL"):
        HTTPTransport(proxy="not a url")


def test_proxy_invalid_url_sync() -> None:
    with pytest.raises(ValueError, match="Failed to parse proxy URL"):
        SyncHTTPTransport(proxy="not a url")

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from queue import Queue
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import trustme
from pyvoy import PyvoyServer

from pyqwest import Client, Headers, HTTPVersion, Request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@dataclass
class Certs:
    ca: bytes
    server_cert: bytes
    server_key: bytes


@pytest.fixture(scope="module")
def certs() -> Certs:
    ca = trustme.CA()
    server = ca.issue_cert("127.0.0.1")
    return Certs(
        ca=ca.cert_pem.bytes(),
        server_cert=server.cert_chain_pems[0].bytes(),
        server_key=server.private_key_pem.bytes(),
    )


@pytest_asyncio.fixture(scope="module")
async def server(certs: Certs) -> AsyncIterator[PyvoyServer]:
    # TODO: Fix issue in pyvoy where if tls_port is 0, separate ports are picked for
    # TLS and QUIC and we cannot find the latter.
    with socket.socket() as s:
        s.bind(("", 0))
        tls_port = s.getsockname()[1]
    async with PyvoyServer(
        "tests.apps.asgi.kitchensink",
        tls_port=tls_port,
        tls_key=certs.server_key,
        tls_cert=certs.server_cert,
        lifespan=False,
        stdout=None,
        stderr=None,
    ) as server:
        yield server


@pytest.fixture(params=["h1", "h2", "h3", "auto"], scope="module")
def http_version(request: pytest.FixtureRequest) -> HTTPVersion | None:
    match request.param:
        case "h1":
            return HTTPVersion.HTTP1
        case "h2":
            return HTTPVersion.HTTP2
        case "h3":
            return HTTPVersion.HTTP3
        case "auto":
            return None
        case _:
            msg = "Invalid HTTP version"
            raise ValueError(msg)


@pytest.fixture(params=["http", "https"])
def url(
    server: PyvoyServer,
    http_version: HTTPVersion | None,
    request: pytest.FixtureRequest,
) -> str:
    match request.param:
        case "http":
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("HTTP/3 over plain HTTP is not supported")
            return f"http://127.0.0.1:{server.listener_port}"
        case "https":
            return f"https://127.0.0.1:{server.listener_port_tls}"
        case _:
            msg = "Invalid scheme"
            raise ValueError(msg)


@pytest.fixture(scope="module")
def client(certs: Certs, http_version: HTTPVersion | None) -> Client:
    return Client(tls_ca_cert=certs.ca, http_version=http_version)


def supports_trailers(http_version: HTTPVersion | None, url: str) -> bool:
    # Currently reqwest trailers patch does not apply to HTTP/3.
    return http_version == HTTPVersion.HTTP2 or (
        http_version is None and url.startswith("https://")
    )


@pytest.mark.asyncio
async def test_basic(client: Client, url: str, http_version: HTTPVersion) -> None:
    req = Request(
        "POST",
        f"{url}/echo",
        headers=[
            ("content-type", "text/plain"),
            ("x-hello", "rust"),
            ("x-hello", "python"),
        ],
        content=b"Hello, World!",
    )
    resp = await client.execute(req)
    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    content = b""
    async for chunk in resp.content:
        content += chunk
    assert content == b"Hello, World!"
    # Didn't send te so should be no trailers
    assert resp.trailers is None
    if http_version is not None:
        assert resp.http_version == http_version
    else:
        if url.startswith("https://"):
            # Currently it seems HTTP/3 is not added to ALPN and must be explicitly
            # set when creating a Client.
            assert resp.http_version == HTTPVersion.HTTP2
        else:
            assert resp.http_version == HTTPVersion.HTTP1


@pytest.mark.asyncio
async def test_iterable_body(client: Client, url: str) -> None:
    req = Request("POST", f"{url}/echo", content=[b"Hello, ", b"World!"])
    resp = await client.execute(req)
    assert resp.status == 200
    content = b""
    async for chunk in resp.content:
        content += chunk
    assert content == b"Hello, World!"


@pytest.mark.asyncio
async def test_empty_request(client: Client, url: str) -> None:
    req = Request("GET", f"{url}/echo")
    resp = await client.execute(req)
    assert resp.status == 200
    content = b""
    async for chunk in resp.content:
        content += chunk
    assert content == b""


@pytest.mark.asyncio
async def test_bidi(client: Client, url: str, http_version: HTTPVersion | None) -> None:
    queue = asyncio.Queue()

    async def request_body() -> AsyncIterator[bytes]:
        while True:
            item: bytes | None = await queue.get()
            if item is None:
                return
            yield item

    req = Request(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=request_body(),
    )

    resp = await client.execute(req)
    assert resp.status == 200
    content = resp.content
    await queue.put(b"Hello!")
    chunk = await anext(content)
    assert chunk == b"Hello!"
    await queue.put(b" World!")
    chunk = await anext(content)
    assert chunk == b" World!"
    await queue.put(None)
    chunk = await anext(content, None)
    assert chunk is None
    if supports_trailers(http_version, url):
        assert resp.trailers is not None
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert resp.trailers is None


@pytest.mark.asyncio
async def test_bidi_sync_iter(
    client: Client, url: str, http_version: HTTPVersion | None
) -> None:
    queue = Queue()

    def request_body() -> Iterator[bytes]:
        while True:
            item: bytes | None = queue.get()
            if item is None:
                return
            yield item

    req = Request(
        "POST",
        f"{url}/echo",
        headers=Headers({"content-type": "text/plain", "te": "trailers"}),
        content=request_body(),
    )

    resp = await client.execute(req)
    assert resp.status == 200
    content = resp.content
    await asyncio.to_thread(queue.put, b"Hello!")
    chunk = await anext(content)
    assert chunk == b"Hello!"
    await asyncio.to_thread(queue.put, b" World!")
    chunk = await anext(content)
    assert chunk == b" World!"
    await asyncio.to_thread(queue.put, None)
    chunk = await anext(content, None)
    assert chunk is None
    if supports_trailers(http_version, url):
        assert resp.trailers is not None
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert resp.trailers is None

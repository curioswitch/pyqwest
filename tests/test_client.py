from __future__ import annotations

import asyncio
import socket
import threading
from dataclasses import dataclass
from queue import Queue
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import trustme
from pyvoy import PyvoyServer

from pyqwest import (
    Client,
    Headers,
    HTTPTransport,
    HTTPVersion,
    SyncClient,
    SyncHTTPTransport,
)

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
    # Workaround https://github.com/seanmonstar/reqwest/issues/2911
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
    tls_port = 0
    while tls_port <= 0:
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
def async_client(certs: Certs, http_version: HTTPVersion | None) -> Client:
    return Client(HTTPTransport(tls_ca_cert=certs.ca, http_version=http_version))


@pytest.fixture(scope="module")
def sync_client(certs: Certs, http_version: HTTPVersion | None) -> SyncClient:
    return SyncClient(
        SyncHTTPTransport(tls_ca_cert=certs.ca, http_version=http_version)
    )


@pytest.fixture(scope="module", params=["async", "sync"])
def client(
    async_client: Client, sync_client: SyncClient, request: pytest.FixtureRequest
) -> Client | SyncClient:
    match request.param:
        case "async":
            return async_client
        case "sync":
            return sync_client
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


def supports_trailers(http_version: HTTPVersion | None, url: str) -> bool:
    # Currently reqwest trailers patch does not apply to HTTP/3.
    return http_version == HTTPVersion.HTTP2 or (
        http_version is None and url.startswith("https://")
    )


async def request_body(queue: asyncio.Queue) -> AsyncIterator[bytes]:
    while True:
        item: bytes | None = await queue.get()
        if item is None:
            return
        yield item


def sync_request_body(queue: Queue) -> Iterator[bytes]:
    while True:
        item: bytes | None = queue.get()
        if item is None:
            return
        yield item


@pytest.mark.asyncio
async def test_basic(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
            client.execute, method, url, headers, req_content
        )
        content = b"".join(resp.content)
    else:
        resp = await client.execute(method, url, headers, req_content)
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert content == b"Hello, World!"
    # Didn't send te so should be no trailers
    assert len(resp.trailers) == 0
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
async def test_iterable_body(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
            client.execute, method, url, content=[b"Hello, ", b"World!"]
        )
        content = b"".join(resp.content)
    else:

        async def req_content() -> AsyncIterator[bytes]:
            yield b"Hello, "
            yield b"World!"

        resp = await client.execute(method, url, content=req_content())
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
    assert content == b"Hello, World!"


@pytest.mark.asyncio
async def test_empty_request(client: Client | SyncClient, url: str) -> None:
    method = "GET"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.execute, method, url)
        content = b"".join(resp.content)
    else:
        resp = await client.execute(method, url)
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
    assert content == b""


@pytest.mark.asyncio
async def test_bidi(
    async_client: Client, url: str, http_version: HTTPVersion | None
) -> None:
    client = async_client
    queue = asyncio.Queue()

    async with await client.execute(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=request_body(queue),
    ) as resp:
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
            assert resp.trailers["x-echo-trailer"] == "last info"
        else:
            assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_bidi_sync(
    sync_client: SyncClient, url: str, http_version: HTTPVersion | None
) -> None:
    client = sync_client
    queue = Queue()

    def run():
        with client.execute(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        ) as resp:
            assert resp.status == 200
            content = resp.content
            queue.put(b"Hello!")
            chunk = next(content)
            assert chunk == b"Hello!"
            queue.put(b" World!")
            chunk = next(content)
            assert chunk == b" World!"
            queue.put(None)
            chunk = next(content, None)
            assert chunk is None
            if supports_trailers(http_version, url):
                assert resp.trailers["x-echo-trailer"] == "last info"
            else:
                assert len(resp.trailers) == 0

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_close_no_read(async_client: Client, url: str) -> None:
    client = async_client
    queue = asyncio.Queue()

    resp = await client.execute(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=request_body(queue),
    )
    assert resp.status == 200
    content = resp.content

    await resp.close()
    chunk = await anext(content, None)
    assert chunk is None


@pytest.mark.asyncio
async def test_close_no_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client
    queue = Queue()

    def run():
        resp = client.execute(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        )
        assert resp.status == 200
        content = resp.content

        resp.close()
        chunk = next(content, None)
        assert chunk is None

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_close_pending_read(async_client: Client, url: str) -> None:
    client = async_client
    queue = asyncio.Queue()

    resp = await client.execute(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=request_body(queue),
    )
    assert resp.status == 200
    content = resp.content

    async def read_content() -> bytes | None:
        return await anext(content, None)

    read_task = asyncio.create_task(read_content())

    await resp.close()
    chunk = await read_task
    assert chunk is None


@pytest.mark.asyncio
async def test_close_pending_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client
    queue = Queue()

    def run():
        resp = client.execute(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        )
        assert resp.status == 200
        content = resp.content

        last_read: bytes | None = b"init"

        def read_content() -> bytes | None:
            nonlocal last_read
            last_read = next(content, None)

        read_thread = threading.Thread(target=read_content)
        read_thread.start()

        resp.close()
        read_thread.join()
        assert last_read is None

    await asyncio.to_thread(run)

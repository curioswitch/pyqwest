from __future__ import annotations

import asyncio
import threading
from queue import Queue
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client, Headers, HTTPVersion, SyncClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


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

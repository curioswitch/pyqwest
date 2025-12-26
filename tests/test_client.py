from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from pyvoy import PyvoyServer

from pyqwest import Client, HTTPVersion, Request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture(scope="module")
async def server() -> AsyncIterator[PyvoyServer]:
    async with PyvoyServer("tests.apps.asgi.kitchensink", lifespan=False) as server:
        yield server


@pytest.fixture
def url(server: PyvoyServer) -> str:
    return f"http://localhost:{server.listener_port}"


@pytest.fixture(scope="module")
def client() -> Client:
    return Client(http_version=HTTPVersion.HTTP2)


@pytest.mark.asyncio
async def test_basic(client: Client, url: str) -> None:
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


@pytest.mark.asyncio
async def test_bidi(client: Client, url: str) -> None:
    queue = asyncio.Queue()

    async def request_body() -> AsyncIterator[bytes]:
        while True:
            yield b""
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
    assert resp.trailers is not None
    assert resp.trailers["x-echo-trailer"] == "last info"

from __future__ import annotations

import asyncio
import socket
from queue import Queue
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client, SyncClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


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
async def test_request_timeout(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # Even with a timeout of zero, headers may still return before timeout,
    # though rarely. There's no way to trigger header timeout deterministically
    # so we just allow it to fail within response handling some times, and
    # try to increase the chance of that by running this test a few times.
    for _ in range(10):
        with pytest.raises(TimeoutError):
            if isinstance(client, SyncClient):

                def run():
                    queue = Queue()
                    resp = client.stream(
                        method, url, content=sync_request_body(queue), timeout=0
                    )
                    next(resp.content)

                await asyncio.to_thread(run)
            else:
                queue = asyncio.Queue()
                resp = await client.stream(
                    method, url, content=request_body(queue), timeout=0
                )
                await anext(resp.content)


@pytest.mark.asyncio
async def test_request_content_timeout(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # Anecdotally, the above test will have one of its runs timeout on the response body
    # in many cases, but check explicitly for good measure.
    with pytest.raises(TimeoutError):
        if isinstance(client, SyncClient):

            def run():
                queue = Queue()
                resp = client.stream(
                    method, url, content=sync_request_body(queue), timeout=0.03
                )
                assert resp.status == 200
                next(resp.content)

            await asyncio.to_thread(run)
        else:
            queue = asyncio.Queue()
            resp = await client.stream(
                method, url, content=request_body(queue), timeout=0.03
            )
            assert resp.status == 200
            await anext(resp.content)


@pytest.mark.asyncio
async def test_connection_error(client: Client | SyncClient, url: str) -> None:
    with socket.socket() as s:
        s.bind(("", 0))
        port = s.getsockname()[1]
    method = "GET"
    url = f"http://localhost:{port}/echo"
    with pytest.raises(ConnectionError):
        if isinstance(client, SyncClient):

            def run():
                client.stream(method, url)

            await asyncio.to_thread(run)
        else:
            await client.stream(method, url)

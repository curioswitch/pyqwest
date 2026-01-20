from __future__ import annotations

import asyncio
import socket
import sys
from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import Client, SyncClient, WriteError

from ._util import SyncRequestBody

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from queue import Queue


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
@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="asyncio.timeout requires Python 3.11+"
)
async def test_request_timeout(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # Even with a timeout of zero, headers may still return before timeout,
    # though rarely. There's no way to trigger header timeout deterministically
    # so we just allow it to fail within response handling some times, and
    # try to increase the chance of that by running this test a few times.
    for _ in range(10):
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            if isinstance(client, SyncClient):

                def run():
                    request_content = SyncRequestBody()
                    with client.stream(
                        method, url, content=request_content, timeout=0
                    ) as resp:
                        next(resp.content)

                await asyncio.to_thread(run)
            else:
                queue = asyncio.Queue()
                async with asyncio.timeout(0):
                    async with client.stream(
                        method, url, content=request_body(queue)
                    ) as resp:
                        await anext(resp.content)


@pytest.mark.asyncio
@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="asyncio.timeout requires Python 3.11+"
)
async def test_response_content_timeout(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # Anecdotally, the above test will have one of its runs timeout on the response body
    # in many cases, but check explicitly for good measure.
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        if isinstance(client, SyncClient):

            def run():
                request_content = SyncRequestBody()
                with client.stream(
                    method, url, content=request_content, timeout=0.03
                ) as resp:
                    assert resp.status == 200
                    next(resp.content)

            await asyncio.to_thread(run)
        else:
            queue = asyncio.Queue()
            async with asyncio.timeout(0.03):
                async with client.stream(
                    method, url, content=request_body(queue)
                ) as resp:
                    assert resp.status == 200
                    await anext(resp.content)


@pytest.mark.asyncio
async def test_connection_error(
    client: Client | SyncClient, client_type: str, url: str
) -> None:
    if client_type in ("async_asgi", "sync_wsgi"):
        pytest.skip("Mock transports don't connect to anything")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    method = "GET"
    url = f"http://localhost:{port}/echo"
    with pytest.raises(ConnectionError):
        if isinstance(client, SyncClient):

            def run():
                client.stream(method, url)

            await asyncio.to_thread(run)
        else:
            async with client.stream(method, url):
                pass


@pytest.mark.asyncio
async def test_request_not_bytes(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    with pytest.raises(WriteError):
        if isinstance(client, SyncClient):

            def request_content_sync():
                yield cast("bytes", 10)

            def run():
                with client.stream(method, url, content=request_content_sync()) as resp:
                    next(resp.content)

            await asyncio.to_thread(run)
        else:

            async def request_content():
                yield cast("bytes", 10)

            async with client.stream(method, url, content=request_content()) as resp:
                await anext(resp.content)

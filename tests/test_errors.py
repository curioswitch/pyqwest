from __future__ import annotations

import socket
from functools import partial
from typing import TYPE_CHECKING, cast

import anyio
import pytest
from anyio import to_thread

from pyqwest import (
    Client,
    HTTPTransport,
    HTTPVersion,
    ReadError,
    SyncClient,
    SyncHTTPTransport,
    WriteError,
)

from ._util import SyncRequestBody, hanging_body

if TYPE_CHECKING:
    from collections.abc import Iterator
    from queue import Queue


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


def sync_request_body(queue: Queue) -> Iterator[bytes]:
    while True:
        item: bytes | None = queue.get()
        if item is None:
            return
        yield item


@pytest.mark.anyio
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
                    request_content = SyncRequestBody()
                    with client.stream(
                        method, url, content=request_content, timeout=0
                    ) as resp:
                        next(resp.content)

                await to_thread.run_sync(run)
            else:
                with anyio.fail_after(0):
                    async with client.stream(
                        method, url, content=hanging_body()
                    ) as resp:
                        await anext(resp.content)


@pytest.mark.anyio
async def test_response_content_timeout(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # Anecdotally, the above test will have one of its runs timeout on the response body
    # in many cases, but check explicitly for good measure.
    with pytest.raises(TimeoutError):
        if isinstance(client, SyncClient):

            def run():
                request_content = SyncRequestBody()
                with client.stream(
                    method, url, content=request_content, timeout=0.03
                ) as resp:
                    assert resp.status == 200
                    next(resp.content)

            await to_thread.run_sync(run)
        else:
            with anyio.fail_after(0.03):
                async with client.stream(method, url, content=hanging_body()) as resp:
                    assert resp.status == 200
                    await anext(resp.content)


@pytest.mark.anyio
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

            await to_thread.run_sync(run)
        else:
            async with client.stream(method, url):
                pass


@pytest.mark.anyio
async def test_connection_error_does_not_advance_request_body(
    http_scheme: str, http_version: HTTPVersion | None
) -> None:
    class RequestBody:
        def __init__(self) -> None:
            self.next_calls = 0
            self.closed = False

        def __iter__(self) -> RequestBody:
            return self

        def __next__(self) -> bytes:
            self.next_calls += 1
            return b"request"

        def close(self) -> None:
            self.closed = True

    class AsyncRequestBody:
        def __init__(self) -> None:
            self.anext_calls = 0
            self.closed = anyio.Event()

        def __aiter__(self) -> AsyncRequestBody:
            return self

        async def __anext__(self) -> bytes:
            self.anext_calls += 1
            return b"request"

        async def aclose(self) -> None:
            self.closed.set()

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        url = f"{http_scheme}://127.0.0.1:{port}/echo"

        request_body = AsyncRequestBody()
        async with HTTPTransport(
            http_version=http_version, connect_timeout=0.1, enable_otel=False
        ) as transport:
            with pytest.raises(ConnectionError):
                await Client(transport).post(url, content=request_body)
        assert request_body.anext_calls == 0
        with anyio.fail_after(5):
            await request_body.closed.wait()

        sync_request_body = RequestBody()
        with (
            SyncHTTPTransport(
                http_version=http_version, connect_timeout=0.1, enable_otel=False
            ) as transport,
            pytest.raises(ConnectionError),
        ):
            await to_thread.run_sync(
                partial(SyncClient(transport).post, url, content=sync_request_body)
            )
        assert sync_request_body.next_calls == 0
        assert sync_request_body.closed


@pytest.mark.anyio
async def test_request_not_bytes(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    # This can also surface either on read or write side based on timing
    with pytest.raises((ReadError, WriteError)):
        if isinstance(client, SyncClient):

            def request_content_sync():
                yield cast("bytes", 10)

            def run():
                with client.stream(method, url, content=request_content_sync()) as resp:
                    next(resp.content)

            await to_thread.run_sync(run)
        else:

            async def request_content():
                yield cast("bytes", 10)

            async with client.stream(method, url, content=request_content()) as resp:
                await anext(resp.content)

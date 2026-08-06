from __future__ import annotations

import asyncio
import socket
import sys
from typing import TYPE_CHECKING, TypeVar, cast

import pytest

from pyqwest import (
    Client,
    HTTPTransport,
    ReadError,
    RemoteProtocolError,
    SyncClient,
    SyncHTTPTransport,
    WriteError,
)

from ._util import (
    BAD_CHUNKED_FRAMING,
    GARBAGE_RESPONSE,
    MALFORMED_STATUS_LINE,
    NO_RESPONSE,
    TRUNCATED_CHUNKED_RESPONSE,
    TRUNCATED_RESPONSE,
    SyncRequestBody,
    raw_server,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator
    from queue import Queue

_Test = TypeVar("_Test", bound="Callable[..., object]")


# Applied per test rather than as a module-level pytestmark because the protocol
# error tests at the bottom of this file serve their own raw socket and so do not
# take the shared server fixtures these parametrize.
def uses_h2_server(fn: _Test) -> _Test:
    fn = pytest.mark.parametrize("http_version", ["h2"], indirect=True)(fn)
    return pytest.mark.parametrize("http_scheme", ["http"], indirect=True)(fn)


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
@uses_h2_server
async def test_request_timeout(client: Client | SyncClient, url: str) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("asyncio.timeout requires Python 3.11+")
        return
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
@uses_h2_server
async def test_response_content_timeout(client: Client | SyncClient, url: str) -> None:
    if sys.version_info < (3, 11):
        pytest.skip("asyncio.timeout requires Python 3.11+")
        return
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
@uses_h2_server
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
@uses_h2_server
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

            await asyncio.to_thread(run)
        else:

            async def request_content():
                yield cast("bytes", 10)

            async with client.stream(method, url, content=request_content()) as resp:
                await anext(resp.content)


# Each of these is a way for the peer to break HTTP framing, and each is served
# over a raw socket so the fault is exactly what the test names. They are
# HTTP/1.1 by construction, so unlike the tests above they build their own
# transport rather than using the parametrized fixtures.
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
        async with HTTPTransport() as transport:
            with pytest.raises(RemoteProtocolError):
                await Client(transport).get(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", PROTOCOL_FAULTS)
async def test_sync_protocol_error(response: bytes) -> None:
    def run() -> None:
        with (
            raw_server(response) as url,
            SyncHTTPTransport() as transport,
            pytest.raises(RemoteProtocolError),
        ):
            SyncClient(transport).get(url)

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_async_response_reset_is_read_error() -> None:
    # A response cut short by a reset is a broken connection rather than a
    # protocol violation, so it must not be swept up by the tests above.
    with raw_server(TRUNCATED_RESPONSE, reset=True) as url:
        async with HTTPTransport() as transport:
            # Usually a read error but timing can make it a write error,
            # especially on Windows.
            with pytest.raises((ReadError, WriteError)):
                await Client(transport).get(url)


@pytest.mark.asyncio
async def test_sync_response_reset_is_read_error() -> None:
    def run() -> None:
        with (
            raw_server(TRUNCATED_RESPONSE, reset=True) as url,
            SyncHTTPTransport() as transport,
            pytest.raises((ReadError, WriteError)),
        ):
            SyncClient(transport).get(url)

    await asyncio.to_thread(run)

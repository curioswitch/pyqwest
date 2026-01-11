from __future__ import annotations

import asyncio
from queue import Queue
from typing import TYPE_CHECKING

import pytest

from pyqwest import (
    Client,
    HTTPTransport,
    Request,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    get_default_sync_transport,
    get_default_transport,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


@pytest.mark.asyncio
async def test_default_transport(url: str) -> None:
    transport = get_default_transport()
    url = f"{url}/echo"
    res = await transport.execute(Request("GET", url))
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_sync_transport(url: str) -> None:
    transport = get_default_sync_transport()
    url = f"{url}/echo"
    res = await asyncio.to_thread(transport.execute, SyncRequest("GET", url))
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_client(url: str) -> None:
    client = Client()
    url = f"{url}/echo"
    res = await client.get(url)
    assert res.status == 200
    assert res.content == b""


@pytest.mark.asyncio
async def test_default_sync_client(url: str) -> None:
    client = SyncClient()
    url = f"{url}/echo"
    res = await asyncio.to_thread(client.get, url)
    assert res.status == 200
    assert res.content == b""


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.asyncio
async def test_transport_options(url: str) -> None:
    transport = HTTPTransport(
        timeout=0.001,
        connect_timeout=10,
        read_timeout=20,
        pool_idle_timeout=30,
        pool_max_idle_per_host=5,
        tcp_keepalive_interval=100,
        enable_gzip=True,
        enable_brotli=True,
        enable_zstd=True,
        use_system_dns=True,
    )

    async def request_content() -> AsyncIterator[bytes]:
        await asyncio.sleep(1)
        yield b"hello"

    url = f"{url}/echo"
    with pytest.raises(TimeoutError):
        res = await transport.execute(Request("POST", url, content=request_content()))
        await res.read_full()


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.asyncio
async def test_sync_transport_options(url: str) -> None:
    transport = SyncHTTPTransport(
        timeout=0.001,
        connect_timeout=10,
        read_timeout=20,
        pool_idle_timeout=30,
        pool_max_idle_per_host=5,
        tcp_keepalive_interval=100,
        enable_gzip=True,
        enable_brotli=True,
        enable_zstd=True,
        use_system_dns=True,
    )

    queue = Queue()

    def request_content() -> Iterator[bytes]:
        queue.get()
        yield b""

    url = f"{url}/echo"
    with (
        pytest.raises(TimeoutError),
        transport.execute(SyncRequest("POST", url, content=request_content())) as res,
    ):
        res.read_full()

    # Make sure the generator cleans up
    queue.put(None)

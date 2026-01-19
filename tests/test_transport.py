from __future__ import annotations

import asyncio
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

from ._util import SyncRequestBody

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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
    res = await asyncio.to_thread(transport.execute_sync, SyncRequest("GET", url))
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


@pytest.mark.asyncio
async def test_status_codes(url: str, subtests: pytest.Subtests) -> None:
    client = Client()
    url = f"{url}/echo"
    for i in range(200, 599):
        with subtests.test(f"status={i}"):
            res = await client.get(url, {"x-response-status": str(i)})
            assert res.status == i


@pytest.mark.asyncio
async def test_status_codes_sync(url: str, subtests: pytest.Subtests) -> None:
    client = SyncClient()
    url = f"{url}/echo"
    for i in range(200, 599):
        with subtests.test(f"status={i}"):
            res = await asyncio.to_thread(
                client.get, url, {"x-response-status": str(i)}
            )
            assert res.status == i


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.asyncio
async def test_transport_options(url: str) -> None:
    async with HTTPTransport(
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
    ) as transport:

        async def request_content() -> AsyncIterator[bytes]:
            await asyncio.sleep(1)
            yield b"hello"

        url = f"{url}/echo"
        with pytest.raises(TimeoutError):
            async with await transport.execute(
                Request("POST", url, content=request_content())
            ) as res:
                async for _ in res.content:
                    pass

    await transport.aclose()  # double close allowed

    with pytest.raises(RuntimeError, match="already closed transport"):
        await transport.execute(Request("GET", url))

    with pytest.raises(RuntimeError, match="already closed transport"):
        await Client(transport).get(url)


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.asyncio
async def test_sync_transport_options(url: str) -> None:
    with SyncHTTPTransport(
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
    ) as transport:
        request_content = SyncRequestBody()

        url = f"{url}/echo"
        with (
            pytest.raises(TimeoutError),
            transport.execute_sync(
                SyncRequest("POST", url, content=request_content)
            ) as res,
        ):
            b"".join(res.content)

    transport.close()  # double close allowed
    with pytest.raises(RuntimeError, match="already closed transport"):
        transport.execute_sync(SyncRequest("GET", url))

    with pytest.raises(RuntimeError, match="already closed transport"):
        SyncClient(transport).get(url)

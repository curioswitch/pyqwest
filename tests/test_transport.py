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
    TooManyRedirects,
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


@pytest.mark.asyncio
async def test_cookie_store(url: str) -> None:
    async with HTTPTransport(enable_cookie_store=True) as transport:
        client = Client(transport)
        await client.get(f"{url}/set-cookie")
        res = await client.get(f"{url}/get-cookie")
        assert res.content == b"testcookie=hello"


@pytest.mark.asyncio
async def test_cookie_store_disabled(url: str) -> None:
    async with HTTPTransport() as transport:
        client = Client(transport)
        await client.get(f"{url}/set-cookie")
        res = await client.get(f"{url}/get-cookie")
        assert res.content == b""


@pytest.mark.asyncio
async def test_cookie_store_sync(url: str) -> None:
    with SyncHTTPTransport(enable_cookie_store=True) as transport:
        client = SyncClient(transport)
        await asyncio.to_thread(client.get, f"{url}/set-cookie")
        res = await asyncio.to_thread(client.get, f"{url}/get-cookie")
        assert res.content == b"testcookie=hello"


@pytest.mark.asyncio
async def test_cookie_store_sync_disabled(url: str) -> None:
    with SyncHTTPTransport() as transport:
        client = SyncClient(transport)
        await asyncio.to_thread(client.get, f"{url}/set-cookie")
        res = await asyncio.to_thread(client.get, f"{url}/get-cookie")
        assert res.content == b""


@pytest.mark.asyncio
async def test_redirects_disabled(url: str) -> None:
    async with HTTPTransport(follow_redirects=False) as transport:
        res = await Client(transport).get(f"{url}/redirect")
        assert res.status == 302
        assert res.headers["location"] == "/echo"


@pytest.mark.asyncio
async def test_follow_redirects(url: str) -> None:
    async with HTTPTransport() as transport:
        res = await Client(transport).get(f"{url}/redirect?n=3")
        assert res.status == 200
        assert res.headers["x-echo-method"] == "GET"


@pytest.mark.asyncio
async def test_follow_redirects_too_many(url: str) -> None:
    async with HTTPTransport(max_redirects=2) as transport:
        with pytest.raises(TooManyRedirects):
            await Client(transport).get(f"{url}/redirect?n=5")


@pytest.mark.asyncio
async def test_redirects_disabled_sync(url: str) -> None:
    with SyncHTTPTransport(follow_redirects=False) as transport:
        res = await asyncio.to_thread(SyncClient(transport).get, f"{url}/redirect")
        assert res.status == 302
        assert res.headers["location"] == "/echo"


@pytest.mark.asyncio
async def test_follow_redirects_sync(url: str) -> None:
    with SyncHTTPTransport() as transport:
        res = await asyncio.to_thread(SyncClient(transport).get, f"{url}/redirect?n=3")
        assert res.status == 200
        assert res.headers["x-echo-method"] == "GET"


@pytest.mark.asyncio
async def test_follow_redirects_too_many_sync(url: str) -> None:
    with (
        SyncHTTPTransport(max_redirects=2) as transport,
        pytest.raises(TooManyRedirects),
    ):
        await asyncio.to_thread(SyncClient(transport).get, f"{url}/redirect?n=5")


@pytest.mark.asyncio
async def test_request_body_task_cancelled_on_dropped_response(url: str) -> None:
    body_closed = asyncio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            yield b"hello"
            await asyncio.Event().wait()
        finally:
            body_closed.set()

    async with HTTPTransport() as transport:
        res = await transport.execute(Request("POST", f"{url}/echo", content=content()))
        assert res.status == 200
        # Dropping the response without closing it must still cancel the
        # request body task, or it would hang on the generator forever.
        del res
        await asyncio.wait_for(body_closed.wait(), timeout=5)


@pytest.mark.asyncio
async def test_request_body_task_cancelled_on_cancelled_execute(url: str) -> None:
    body_started = asyncio.Event()
    body_closed = asyncio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            body_started.set()
            await asyncio.Event().wait()
            yield b""
        finally:
            body_closed.set()

    async with HTTPTransport() as transport:
        fut = asyncio.ensure_future(
            transport.execute(Request("POST", f"{url}/read_all", content=content()))
        )
        await body_started.wait()
        fut.cancel()
        with pytest.raises(asyncio.CancelledError):
            await fut
        await asyncio.wait_for(body_closed.wait(), timeout=5)

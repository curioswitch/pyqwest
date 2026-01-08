from __future__ import annotations

import asyncio
import ssl
import sys
from typing import TYPE_CHECKING

import aiohttp
import httpx
import niquests
import pytest
import pytest_asyncio

from pyqwest import Client, HTTPTransport, HTTPVersion
from pyqwest.httpx import AsyncPyQwestTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    import pytest_benchmark.fixture

    from .conftest import Certs

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2", "h3", "auto"], indirect=True),
]

CONCURRENCY = 10
TASK_SIZE = 30


@pytest.fixture(
    params=["pyqwest", "aiohttp", "httpx", "httpx_pyqwest", "niquests"], scope="module"
)
def library(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="module")
def async_runner() -> Iterator[asyncio.Runner]:
    with asyncio.Runner() as runner:
        yield runner


@pytest_asyncio.fixture(scope="module")
async def benchmark_client_async(
    async_client: Client,
    async_transport: HTTPTransport,
    certs: Certs,
    http_version: HTTPVersion | None,
    library: str,
    async_runner: asyncio.Runner,
) -> AsyncIterator[
    Client | httpx.AsyncClient | aiohttp.ClientSession | niquests.AsyncSession
]:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.load_verify_locations(cadata=certs.ca.decode())
    match library:
        case "aiohttp":
            if http_version != HTTPVersion.HTTP1:
                pytest.skip("aiohttp only supports HTTP/1")

            async def _create_session() -> aiohttp.ClientSession:
                return aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=ssl_ctx), timeout=None
                )

            session = await asyncio.to_thread(async_runner.run, _create_session())
            try:
                yield session
            finally:
                await asyncio.to_thread(async_runner.run, session.close())
        case "httpx":
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("httpx does not support HTTP/3")
            async with httpx.AsyncClient(
                verify=ssl_ctx,
                http1=(http_version in (HTTPVersion.HTTP1, None)),
                http2=(http_version in (HTTPVersion.HTTP2, None)),
                timeout=None,  # noqa: S113
                trust_env=False,
            ) as client:
                yield client
        case "httpx_pyqwest":
            async with httpx.AsyncClient(
                transport=AsyncPyQwestTransport(async_transport)
            ) as client:
                yield client
        case "niquests":
            # Errors like File descriptor 17 is used by transport
            pytest.skip("niquests seems to not be reliable")
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("TODO: Debug SNI 127.0.0.1 issue")
            async with niquests.AsyncSession(
                disable_http1=(http_version not in (HTTPVersion.HTTP1, None)),
                disable_http2=(http_version not in (HTTPVersion.HTTP2, None)),
                disable_http3=(http_version not in (HTTPVersion.HTTP3, None)),
            ) as client:
                client.verify = certs.ca
                yield client
        case "pyqwest":
            yield async_client


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="asyncio.Runner requires Python 3.11+"
)
def test_benchmark_async(
    benchmark: pytest_benchmark.fixture.BenchmarkFixture,
    benchmark_client_async: Client
    | aiohttp.ClientSession
    | httpx.AsyncClient
    | niquests.AsyncSession,
    url: str,
    async_runner: asyncio.Runner,
) -> None:
    method = "POST"
    target_url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    body = b"Hello, World!"

    execute_request: Callable[[], Awaitable[None]]
    match benchmark_client_async:
        case Client():

            async def execute_request_pyqwest() -> None:
                for _ in range(TASK_SIZE):
                    async with await benchmark_client_async.stream(
                        method, target_url, headers, body
                    ) as res:
                        assert res.status == 200
                        async for _chunk in res.content:
                            pass

            execute_request = execute_request_pyqwest
        case aiohttp.ClientSession():

            async def execute_request_aiohttp() -> None:
                for _ in range(TASK_SIZE):
                    async with benchmark_client_async.request(
                        method, target_url, headers=headers, data=body
                    ) as res:
                        assert res.status == 200
                        async for _chunk in res.content.iter_chunked(1024):
                            pass

            execute_request = execute_request_aiohttp
        case httpx.AsyncClient():

            async def execute_request_httpx() -> None:
                for _ in range(TASK_SIZE):
                    async with benchmark_client_async.stream(
                        method, target_url, headers=headers, content=body
                    ) as res:
                        assert res.status_code == 200
                        async for _chunk in res.aiter_bytes():
                            pass

            execute_request = execute_request_httpx
        case niquests.AsyncSession():

            async def execute_request_niquests() -> None:
                for _ in range(TASK_SIZE):
                    res = await benchmark_client_async.request(
                        method, target_url, data=body, stream=True
                    )
                    assert res.status_code == 200
                    async for _chunk in await res.iter_content():
                        pass

            execute_request = execute_request_niquests

    async def execute_requests() -> None:
        tasks = [asyncio.create_task(execute_request()) for _ in range(CONCURRENCY)]
        await asyncio.gather(*tasks)

    @benchmark
    def run_benchmark() -> None:
        async_runner.run(execute_requests())

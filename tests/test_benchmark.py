from __future__ import annotations

import asyncio
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import aiohttp
import httpx
import niquests
import pytest
import pytest_asyncio

from pyqwest import Client, HTTPTransport, HTTPVersion, SyncClient, SyncHTTPTransport
from pyqwest.httpx import AsyncPyqwestTransport, PyqwestTransport

try:
    import uvloop

    new_event_loop = uvloop.new_event_loop
except ImportError:
    import winloop  # pyright: ignore[reportMissingImports]

    new_event_loop = winloop.new_event_loop

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    import pytest_benchmark.fixture

    from .conftest import Certs

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2", "h3"], indirect=True),
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
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        yield runner


@pytest.fixture(scope="module")
def sync_runner() -> Iterator[ThreadPoolExecutor]:
    with ThreadPoolExecutor(CONCURRENCY) as executor:
        yield executor


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
                transport=AsyncPyqwestTransport(async_transport)
            ) as client:
                yield client
        case "niquests":
            pytest.skip("seems to leak file descriptors")
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("Connection aborted error")
            async with niquests.AsyncSession(
                disable_http1=(http_version not in (HTTPVersion.HTTP1, None)),
                disable_http2=(http_version not in (HTTPVersion.HTTP2, None)),
                disable_http3=(http_version not in (HTTPVersion.HTTP3, None)),
            ) as client:
                client.verify = certs.ca
                yield client
        case "pyqwest":
            yield async_client


@pytest.fixture(scope="module")
def benchmark_client_sync(
    sync_client: SyncClient,
    sync_transport: SyncHTTPTransport,
    certs: Certs,
    http_version: HTTPVersion | None,
    library: str,
) -> Iterator[SyncClient | httpx.Client | niquests.Session]:
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.load_verify_locations(cadata=certs.ca.decode())
    match library:
        case "aiohttp":
            pytest.skip("aiohttp does not have a sync client")
        case "httpx":
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("httpx does not support HTTP/3")
            with httpx.Client(
                verify=ssl_ctx,
                http1=(http_version in (HTTPVersion.HTTP1, None)),
                http2=(http_version in (HTTPVersion.HTTP2, None)),
                timeout=None,  # noqa: S113
                trust_env=False,
            ) as client:
                yield client
        case "httpx_pyqwest":
            with httpx.Client(transport=PyqwestTransport(sync_transport)) as client:
                yield client
        case "niquests":
            pytest.skip("seems to leak file descriptors")
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("Connection aborted error")
            with niquests.Session(
                disable_http1=(http_version not in (HTTPVersion.HTTP1, None)),
                disable_http2=(http_version not in (HTTPVersion.HTTP2, None)),
                disable_http3=(http_version not in (HTTPVersion.HTTP3, None)),
            ) as client:
                client.verify = certs.ca
                yield client
        case "pyqwest":
            yield sync_client


@pytest.mark.skipif(
    sys.version_info < (3, 11), reason="asyncio.Runner requires Python 3.11+"
)
@pytest.mark.parametrize("content_size", [0, 1024, 1024 * 1024])
def test_benchmark_async(
    benchmark: pytest_benchmark.fixture.BenchmarkFixture,
    benchmark_client_async: Client
    | aiohttp.ClientSession
    | httpx.AsyncClient
    | niquests.AsyncSession,
    url: str,
    content_size: int,
    async_runner: asyncio.Runner,
) -> None:
    method = "POST"
    target_url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    match content_size:
        case 0:
            body = b""
        case 1024:
            body = b"A" * 1024
        case _:

            async def generate_body(size) -> AsyncIterator[bytes]:
                chunk = b"A" * 1024
                for _ in range(size // 1024):
                    yield chunk

            body = generate_body(content_size)

    execute_request: Callable[[], Awaitable[None]]
    match benchmark_client_async:
        case Client():

            async def execute_request_pyqwest() -> None:
                for _ in range(TASK_SIZE):
                    async with benchmark_client_async.stream(
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
                    with await benchmark_client_async.request(
                        method, target_url, data=body, stream=True
                    ) as res:
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


@pytest.mark.parametrize("content_size", [0, 1024, 1024 * 1024])
def test_benchmark_sync(
    benchmark: pytest_benchmark.fixture.BenchmarkFixture,
    benchmark_client_sync: SyncClient | httpx.Client | niquests.Session,
    url: str,
    content_size: int,
    sync_runner: ThreadPoolExecutor,
) -> None:
    method = "POST"
    target_url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    match content_size:
        case 0:
            body = b""
        case 1024:
            body = b"A" * 1024
        case _:

            def generate_body(size) -> Iterator[bytes]:
                chunk = b"A" * 1024
                for _ in range(size // 1024):
                    yield chunk

            body = generate_body(content_size)

    execute_request: Callable[[], None]
    match benchmark_client_sync:
        case SyncClient():

            def execute_request_pyqwest() -> None:
                for _ in range(TASK_SIZE):
                    with benchmark_client_sync.stream(
                        method, target_url, headers, body
                    ) as res:
                        assert res.status == 200
                        for _chunk in res.content:
                            pass

            execute_request = execute_request_pyqwest
        case httpx.Client():

            def execute_request_httpx() -> None:
                for _ in range(TASK_SIZE):
                    with benchmark_client_sync.stream(
                        method, target_url, headers=headers, content=body
                    ) as res:
                        assert res.status_code == 200
                        for _chunk in res.iter_bytes():
                            pass

            execute_request = execute_request_httpx
        case niquests.Session():

            def execute_request_niquests() -> None:
                for _ in range(TASK_SIZE):
                    with benchmark_client_sync.request(
                        method, target_url, data=body, stream=True
                    ) as res:
                        assert res.status_code == 200
                        for _chunk in res.iter_content():
                            pass

            execute_request = execute_request_niquests

    @benchmark
    def execute_requests() -> None:
        tasks = [sync_runner.submit(execute_request) for _ in range(CONCURRENCY)]
        for task in tasks:
            task.result()

"""Balancing requests over several connections per origin.

The server here advertises a small `SETTINGS_MAX_CONCURRENT_STREAMS` and holds
`/stream` responses open, so the tests can see whether requests beyond the
limit queue on the one connection hyper keeps or start on another one.
"""

from __future__ import annotations

import gc
import socket
import threading
import time
from typing import TYPE_CHECKING, cast

import anyio
import pytest
from anyio import to_thread
from opentelemetry.test.test_base import TestBase

from pyqwest import (
    Client,
    HTTPTransport,
    HTTPVersion,
    Request,
    Response,
    StreamError,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
)

from ._h2server import H2Server, h2_server

if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry.sdk.metrics._internal.point import Gauge, Metric

    from .conftest import Certs

STREAM_LIMIT = 2
"""The concurrent streams the test server allows per connection."""

QUEUED_WAIT = 0.5
"""How long a request must stay pending to count as queued."""


@pytest.fixture
def h2server() -> Iterator[H2Server]:
    with h2_server(max_concurrent_streams=STREAM_LIMIT) as server:
        yield server


def loads(transport: HTTPTransport | SyncHTTPTransport) -> list[int] | None:
    return transport._connection_loads  # ty: ignore[unresolved-attribute]


def wait_for_loads(
    transport: HTTPTransport | SyncHTTPTransport, expected: list[int]
) -> None:
    """Waits for a release that happens on deallocation, which is not
    necessarily synchronous.
    """
    deadline = time.monotonic() + 5
    while loads(transport) != expected:
        if time.monotonic() > deadline:
            pytest.fail(f"connection loads are {loads(transport)}, expected {expected}")
        gc.collect()
        time.sleep(0.01)


async def open_stream(transport: HTTPTransport, server: H2Server) -> Response:
    return await transport.execute(Request("GET", f"{server.url}/stream"))


async def drain(response: Response) -> None:
    async for _ in response.content:
        pass


def open_stream_sync(transport: SyncHTTPTransport, server: H2Server) -> SyncResponse:
    return transport.execute_sync(SyncRequest("GET", f"{server.url}/stream"))


def drain_sync(response: SyncResponse) -> None:
    for _ in response.content:
        pass


def closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{sock.getsockname()[1]}"


@pytest.mark.anyio
async def test_default_transport_queues_at_stream_limit(h2server: H2Server) -> None:
    async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        assert loads(transport) is None
        held = [await open_stream(transport, h2server) for _ in range(STREAM_LIMIT)]
        assert h2server.connections == 1
        assert h2server.streams_received == STREAM_LIMIT

        started = anyio.Event()
        extra: list[Response] = []

        async def open_extra() -> None:
            extra.append(await open_stream(transport, h2server))
            started.set()

        async with anyio.create_task_group() as tg:
            tg.start_soon(open_extra)
            with anyio.move_on_after(QUEUED_WAIT):
                await started.wait()
            # hyper keeps the one connection and queues the request on it.
            assert not started.is_set()
            assert h2server.connections == 1
            assert h2server.streams_received == STREAM_LIMIT

            h2server.finish_stream()
            await drain(held.pop(0))
            with anyio.fail_after(5):
                await started.wait()
            assert h2server.connections == 1
            h2server.finish_all()
            for response in [*held, *extra]:
                await drain(response)


@pytest.mark.anyio
async def test_opens_another_connection_at_stream_limit(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        assert loads(transport) == [0]
        responses = [
            await open_stream(transport, h2server) for _ in range(STREAM_LIMIT + 1)
        ]
        # Every stream started, the extra one on a second connection.
        assert h2server.connections == 2
        assert h2server.streams_received == STREAM_LIMIT + 1
        assert loads(transport) == [STREAM_LIMIT, 1]

        h2server.finish_all()
        for response in responses:
            await drain(response)
        assert loads(transport) == [0, 0]


@pytest.mark.anyio
async def test_max_connections(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1, max_connections=2
    ) as transport:
        responses = [await open_stream(transport, h2server) for _ in range(3)]
        # At the cap, the least loaded connection takes the request, the
        # first one on a tie.
        assert h2server.connections == 2
        assert loads(transport) == [2, 1]

        h2server.finish_all()
        for response in responses:
            await drain(response)
        assert loads(transport) == [0, 0]


@pytest.mark.anyio
async def test_least_loaded_connection(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=2
    ) as transport:
        first = await open_stream(transport, h2server)
        responses = [await open_stream(transport, h2server) for _ in range(2)]
        assert loads(transport) == [2, 1]

        # Finishing the oldest stream frees a slot on the first connection.
        h2server.finish_stream()
        await drain(first)
        assert loads(transport) == [1, 1]

        # The first of the equally loaded connections gets the next request,
        # then the other, then the set grows again.
        responses.append(await open_stream(transport, h2server))
        assert loads(transport) == [2, 1]
        responses.append(await open_stream(transport, h2server))
        assert loads(transport) == [2, 2]
        responses.append(await open_stream(transport, h2server))
        assert loads(transport) == [2, 2, 1]
        assert h2server.connections == 3

        h2server.finish_all()
        for response in responses:
            await drain(response)
        assert loads(transport) == [0, 0, 0]


@pytest.mark.anyio
async def test_releases_on_close(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = await open_stream(transport, h2server)
        assert loads(transport) == [1]
        await response.aclose()
        assert loads(transport) == [0]
        await to_thread.run_sync(h2server.wait_for, "streams_reset", 1)
        # Closing again releases nothing more.
        await response.aclose()
        assert loads(transport) == [0]


@pytest.mark.anyio
async def test_releases_on_drop(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = await open_stream(transport, h2server)
        assert loads(transport) == [1]
        del response
        await to_thread.run_sync(wait_for_loads, transport, [0])
        await to_thread.run_sync(h2server.wait_for, "streams_reset", 1)


@pytest.mark.anyio
async def test_releases_on_request_error(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1
    ) as transport:
        with pytest.raises(ConnectionError):
            await transport.execute(Request("GET", closed_port_url()))
        assert loads(transport) == [0]
        # The failure did not grow the set either.
        await drain(await transport.execute(Request("GET", f"{h2server.url}/echo")))
        assert h2server.connections == 1


@pytest.mark.anyio
async def test_releases_on_read_error(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = await open_stream(transport, h2server)
        assert loads(transport) == [1]
        h2server.reset_stream()
        with pytest.raises(StreamError):
            await drain(response)
        assert loads(transport) == [0]
        # Reading again releases nothing more.
        await drain(response)
        assert loads(transport) == [0]


@pytest.mark.anyio
async def test_releases_full_response(h2server: H2Server) -> None:
    async with HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1
    ) as transport:
        client = Client(transport)
        for _ in range(3):
            response = await client.get(f"{h2server.url}/echo")
            assert response.content == b"hello"
            assert loads(transport) == [0]
        assert h2server.connections == 1


@pytest.mark.anyio
async def test_closed_transport(h2server: H2Server) -> None:
    transport = HTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1
    )
    response = await open_stream(transport, h2server)
    await transport.aclose()
    assert loads(transport) is None
    with pytest.raises(RuntimeError, match="closed transport"):
        await open_stream(transport, h2server)
    # A response outlives its transport.
    h2server.finish_all()
    await drain(response)


@pytest.mark.anyio
async def test_metrics(h2server: H2Server) -> None:
    test_base = TestBase()
    test_base.setUp()
    try:
        async with HTTPTransport(
            http_version=HTTPVersion.HTTP2,
            max_streams_per_connection=1,
            meter_provider=test_base.meter_provider,
        ) as transport:
            responses = [await open_stream(transport, h2server) for _ in range(2)]
            assert balancer_metrics(test_base) == {
                "pyqwest.transport.connections": 2,
                "pyqwest.transport.in_flight_requests": 2,
            }
            h2server.finish_all()
            for response in responses:
                await drain(response)
            assert balancer_metrics(test_base) == {
                "pyqwest.transport.connections": 2,
                "pyqwest.transport.in_flight_requests": 0,
            }
        # A closed transport observes nothing.
        assert balancer_metrics(test_base) == {}
    finally:
        test_base.tearDown()


def balancer_metrics(test_base: TestBase) -> dict[str, int]:
    metrics = cast("list[Metric]", test_base.get_sorted_metrics())
    values: dict[str, int] = {}
    for metric in metrics:
        if not metric.name.startswith("pyqwest.transport."):
            continue
        data = cast("Gauge", metric.data)
        assert len(data.data_points) == 1
        assert data.data_points[0].attributes == {}
        values[metric.name] = int(data.data_points[0].value)
    return values


def test_sync_default_transport_queues_at_stream_limit(h2server: H2Server) -> None:
    with SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        assert loads(transport) is None
        held = [open_stream_sync(transport, h2server) for _ in range(STREAM_LIMIT)]
        assert h2server.connections == 1

        extra: list[SyncResponse] = []
        thread = threading.Thread(
            target=lambda: extra.append(open_stream_sync(transport, h2server)),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=QUEUED_WAIT)
        assert thread.is_alive()
        assert h2server.connections == 1
        assert h2server.streams_received == STREAM_LIMIT

        h2server.finish_stream()
        drain_sync(held.pop(0))
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert h2server.connections == 1
        h2server.finish_all()
        for response in [*held, *extra]:
            drain_sync(response)


def test_sync_opens_another_connection_at_stream_limit(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        responses = [
            open_stream_sync(transport, h2server) for _ in range(STREAM_LIMIT + 1)
        ]
        assert h2server.connections == 2
        assert h2server.streams_received == STREAM_LIMIT + 1
        assert loads(transport) == [STREAM_LIMIT, 1]

        h2server.finish_all()
        for response in responses:
            drain_sync(response)
        assert loads(transport) == [0, 0]


def test_sync_max_connections(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1, max_connections=2
    ) as transport:
        responses = [open_stream_sync(transport, h2server) for _ in range(3)]
        assert h2server.connections == 2
        assert loads(transport) == [2, 1]

        h2server.finish_all()
        for response in responses:
            drain_sync(response)
        assert loads(transport) == [0, 0]


def test_sync_least_loaded_connection(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=2
    ) as transport:
        first = open_stream_sync(transport, h2server)
        responses = [open_stream_sync(transport, h2server) for _ in range(2)]
        assert loads(transport) == [2, 1]

        h2server.finish_stream()
        drain_sync(first)
        assert loads(transport) == [1, 1]

        responses.append(open_stream_sync(transport, h2server))
        assert loads(transport) == [2, 1]
        responses.append(open_stream_sync(transport, h2server))
        assert loads(transport) == [2, 2]
        responses.append(open_stream_sync(transport, h2server))
        assert loads(transport) == [2, 2, 1]
        assert h2server.connections == 3

        h2server.finish_all()
        for response in responses:
            drain_sync(response)
        assert loads(transport) == [0, 0, 0]


def test_sync_releases_on_close(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = open_stream_sync(transport, h2server)
        assert loads(transport) == [1]
        response.close()
        assert loads(transport) == [0]
        h2server.wait_for("streams_reset", 1)
        response.close()
        assert loads(transport) == [0]


def test_sync_releases_on_drop(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = open_stream_sync(transport, h2server)
        assert loads(transport) == [1]
        del response
        wait_for_loads(transport, [0])
        h2server.wait_for("streams_reset", 1)


def test_sync_releases_on_request_error(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1
    ) as transport:
        with pytest.raises(ConnectionError):
            transport.execute_sync(SyncRequest("GET", closed_port_url()))
        assert loads(transport) == [0]
        drain_sync(transport.execute_sync(SyncRequest("GET", f"{h2server.url}/echo")))
        assert h2server.connections == 1


def test_sync_releases_on_read_error(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        response = open_stream_sync(transport, h2server)
        assert loads(transport) == [1]
        h2server.reset_stream()
        with pytest.raises(StreamError):
            drain_sync(response)
        assert loads(transport) == [0]
        drain_sync(response)
        assert loads(transport) == [0]


def test_sync_releases_full_response(h2server: H2Server) -> None:
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=1
    ) as transport:
        client = SyncClient(transport)
        for _ in range(3):
            response = client.get(f"{h2server.url}/echo")
            assert response.content == b"hello"
            assert loads(transport) == [0]
        assert h2server.connections == 1


def test_sync_threads_share_connections(h2server: H2Server) -> None:
    """Many threads at once never exceed the stream limit per connection."""
    with SyncHTTPTransport(
        http_version=HTTPVersion.HTTP2, max_streams_per_connection=STREAM_LIMIT
    ) as transport:
        responses: list[SyncResponse] = []
        lock = threading.Lock()

        def open_one() -> None:
            response = open_stream_sync(transport, h2server)
            with lock:
                responses.append(response)

        threads = [threading.Thread(target=open_one) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert len(responses) == 6
        assert loads(transport) == [STREAM_LIMIT, STREAM_LIMIT, STREAM_LIMIT]
        assert h2server.connections == 3

        h2server.finish_all()
        for response in responses:
            drain_sync(response)
        assert loads(transport) == [0, 0, 0]


@pytest.mark.parametrize(
    ("max_streams_per_connection", "max_connections", "message"),
    [
        (0, None, "max_streams_per_connection must be"),
        (1, 0, "max_connections must be"),
        (None, 1, "requires max_streams_per_connection"),
    ],
)
def test_invalid_options(
    client_type: str,
    max_streams_per_connection: int | None,
    max_connections: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        if client_type == "sync":
            SyncHTTPTransport(
                max_streams_per_connection=max_streams_per_connection,
                max_connections=max_connections,
            )
        else:
            HTTPTransport(
                max_streams_per_connection=max_streams_per_connection,
                max_connections=max_connections,
            )


@pytest.mark.parametrize("http_scheme", ["https"], indirect=True)
@pytest.mark.parametrize("http_version", ["h2"], indirect=True)
@pytest.mark.anyio
async def test_cookies_shared_across_connections(
    h2server: H2Server, certs: Certs, http_version: HTTPVersion, url: str
) -> None:
    async with HTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        max_streams_per_connection=1,
        enable_cookie_store=True,
    ) as transport:
        client = Client(transport)
        await client.get(f"{url}/set-cookie")
        assert loads(transport) == [0]
        # A held stream fills the first connection, so the next request opens
        # a second one, which must send the cookie the first stored.
        held = await open_stream(transport, h2server)
        assert loads(transport) == [1]
        response = await client.get(f"{url}/get-cookie")
        assert response.content == b"testcookie=hello"
        assert loads(transport) == [1, 0]
        h2server.finish_all()
        await drain(held)


@pytest.mark.parametrize("http_scheme", ["https"], indirect=True)
@pytest.mark.parametrize("http_version", ["h2"], indirect=True)
def test_sync_cookies_shared_across_connections(
    h2server: H2Server, certs: Certs, http_version: HTTPVersion, url: str
) -> None:
    with SyncHTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        max_streams_per_connection=1,
        enable_cookie_store=True,
    ) as transport:
        client = SyncClient(transport)
        client.get(f"{url}/set-cookie")
        assert loads(transport) == [0]
        held = open_stream_sync(transport, h2server)
        assert loads(transport) == [1]
        response = client.get(f"{url}/get-cookie")
        assert response.content == b"testcookie=hello"
        assert loads(transport) == [1, 0]
        h2server.finish_all()
        drain_sync(held)

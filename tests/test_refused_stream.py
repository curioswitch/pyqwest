"""Requests whose HTTP/2 stream the server refused without processing.

A server retiring a connection sends GOAWAY naming the last stream it may have
processed. A request the client had already sent on a later stream was not
processed (RFC 9113 §6.8), so it is safe to send again, and the transports do
so when the content can be replayed.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio import to_thread

from pyqwest import (
    HTTPTransport,
    HTTPVersion,
    Request,
    Response,
    StreamError,
    StreamErrorCode,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
)

from ._goaway_server import GoawayTestServer
from ._h2_relay import Relay

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


HELD_STREAM_ID = 1
"""The first stream a client opens on a connection."""

NOTICE_STREAM_ID = 2**31 - 1
"""The last stream ID of a GOAWAY that only announces a shutdown."""

NO_ERROR = 0

SETTLE = 0.3
"""How long a resend that must not happen is given to show up. A resend has no
backoff, so it would arrive in a few milliseconds."""

PAYLOAD = b"payload"


@pytest.fixture(scope="module")
def envoy() -> Iterator[GoawayTestServer]:
    server = GoawayTestServer()
    # pyvoy drives its Envoy subprocess with asyncio. A private loop keeps the
    # server independent of the backend the async tests run on.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(server.start())
        try:
            yield server
        finally:
            loop.run_until_complete(server.stop())
    finally:
        loop.close()


@pytest.fixture
def relay(envoy: GoawayTestServer) -> Iterator[Relay]:
    envoy.reset()
    with Relay(envoy.port) as relay:
        yield relay


def retire(relay: Relay) -> None:
    """Checks the GOAWAYs Envoy sent to retire the first connection: the notice,
    then the final one naming the held stream as the last it processed."""
    relay.wait_for_goaways(2)
    assert relay.goaways == [(NOTICE_STREAM_ID, NO_ERROR), (HELD_STREAM_ID, NO_ERROR)]


def deliver(relay: Relay) -> None:
    """Lets the network catch up: the client receives Envoy's GOAWAYs, and the
    racer's frames reach Envoy after it stopped accepting streams."""
    relay.wait_for_held_client()
    relay.release_goaways()
    relay.release_client()


def assert_refused(error: StreamError) -> None:
    assert error.code == StreamErrorCode.REFUSED_STREAM
    assert str(error).startswith("Request failed: ")


async def aread(content: AsyncIterator[bytes | memoryview | bytearray]) -> bytes:
    return b"".join([chunk async for chunk in content])


async def streamed() -> AsyncIterator[bytes]:
    yield PAYLOAD


@pytest.mark.anyio
async def test_async_goaway_resends_on_new_connection(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        held = await transport.execute(Request("GET", f"{relay.url}/held"))
        assert held.status == 200
        await to_thread.run_sync(retire, relay)

        # The client has seen none of it, so the racer goes to the retiring
        # connection.
        relay.hold_client()
        responses: list[Response] = []

        async def race() -> None:
            responses.append(
                await transport.execute(
                    Request("POST", f"{relay.url}/racer", content=PAYLOAD)
                )
            )

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(race)
            await to_thread.run_sync(deliver, relay)

        (response,) = responses
        assert response.status == 200
        assert await aread(response.content) == b"ok"

        # Envoy discarded the refused stream and dispatched only the resend,
        # which came on a new connection with the whole content.
        assert await to_thread.run_sync(envoy.seen) == [
            ("GET", "/held", ""),
            ("POST", "/racer", PAYLOAD.decode()),
        ]
        assert relay.connections == 2
        assert await to_thread.run_sync(envoy.connections) == 2

        # The stream the GOAWAY allowed runs to completion.
        await to_thread.run_sync(envoy.release)
        assert await aread(held.content) == b"held-done"


@pytest.mark.anyio
async def test_async_goaway_streamed_content_is_not_replayed(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        held = await transport.execute(Request("GET", f"{relay.url}/held"))
        assert held.status == 200
        await to_thread.run_sync(retire, relay)

        relay.hold_client()
        errors: list[StreamError] = []

        async def race() -> None:
            try:
                await transport.execute(
                    Request("POST", f"{relay.url}/racer", content=streamed())
                )
            except StreamError as error:
                errors.append(error)

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(race)
            await to_thread.run_sync(deliver, relay)

        (error,) = errors
        assert_refused(error)
        await anyio.sleep(SETTLE)
        # Envoy never dispatched the refused stream, and nothing was resent.
        assert await to_thread.run_sync(envoy.seen) == [("GET", "/held", "")]
        assert relay.connections == 1

        await to_thread.run_sync(envoy.release)
        assert await aread(held.content) == b"held-done"


@pytest.mark.anyio
async def test_async_request_after_goaway_notice_uses_new_connection(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        first = await transport.execute(Request("GET", f"{relay.url}/first"))
        assert await aread(first.content) == b"ok"
        # Only the notice reaches the pooled connection, the way grpc-go starts
        # retiring a connection that reached its maximum age.
        await to_thread.run_sync(relay.wait_for_goaways, 1)
        assert relay.goaways[0] == (NOTICE_STREAM_ID, NO_ERROR)
        relay.release_goaways(1)
        await anyio.sleep(SETTLE)

        # Not even sent to the retiring connection, so streamed content is
        # fine: the request moves to a new connection untouched.
        response = await transport.execute(
            Request("POST", f"{relay.url}/next", content=streamed())
        )
        assert await aread(response.content) == b"ok"
        assert await to_thread.run_sync(envoy.seen) == [
            ("GET", "/first", ""),
            ("POST", "/next", PAYLOAD.decode()),
        ]
        assert relay.connections == 2


def read(content: Iterator[bytes | memoryview | bytearray]) -> bytes:
    return b"".join(content)


def streamed_sync() -> Iterator[bytes]:
    yield PAYLOAD


def test_sync_goaway_resends_on_new_connection(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    with SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        held = transport.execute_sync(SyncRequest("GET", f"{relay.url}/held"))
        assert held.status == 200
        retire(relay)

        relay.hold_client()
        responses: list[SyncResponse] = []

        def race() -> None:
            responses.append(
                transport.execute_sync(
                    SyncRequest("POST", f"{relay.url}/racer", content=PAYLOAD)
                )
            )

        racer = threading.Thread(target=race)
        racer.start()
        deliver(relay)
        racer.join()

        (response,) = responses
        assert response.status == 200
        assert read(response.content) == b"ok"

        assert envoy.seen() == [
            ("GET", "/held", ""),
            ("POST", "/racer", PAYLOAD.decode()),
        ]
        assert relay.connections == 2
        assert envoy.connections() == 2

        envoy.release()
        assert read(held.content) == b"held-done"


def test_sync_goaway_streamed_content_is_not_replayed(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    with SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        held = transport.execute_sync(SyncRequest("GET", f"{relay.url}/held"))
        assert held.status == 200
        retire(relay)

        relay.hold_client()
        errors: list[StreamError] = []

        def race() -> None:
            try:
                transport.execute_sync(
                    SyncRequest("POST", f"{relay.url}/racer", content=streamed_sync())
                )
            except StreamError as error:
                errors.append(error)

        racer = threading.Thread(target=race)
        racer.start()
        deliver(relay)
        racer.join()

        (error,) = errors
        assert_refused(error)
        time.sleep(SETTLE)
        assert envoy.seen() == [("GET", "/held", "")]
        assert relay.connections == 1

        envoy.release()
        assert read(held.content) == b"held-done"


def test_sync_request_after_goaway_notice_uses_new_connection(
    envoy: GoawayTestServer, relay: Relay
) -> None:
    relay.hold_goaways()
    with SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
        first = transport.execute_sync(SyncRequest("GET", f"{relay.url}/first"))
        assert read(first.content) == b"ok"
        relay.wait_for_goaways(1)
        assert relay.goaways[0] == (NOTICE_STREAM_ID, NO_ERROR)
        relay.release_goaways(1)
        time.sleep(SETTLE)

        response = transport.execute_sync(
            SyncRequest("POST", f"{relay.url}/next", content=streamed_sync())
        )
        assert read(response.content) == b"ok"
        assert envoy.seen() == [
            ("GET", "/first", ""),
            ("POST", "/next", PAYLOAD.decode()),
        ]
        assert relay.connections == 2

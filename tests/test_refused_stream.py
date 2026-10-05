"""Requests whose HTTP/2 stream the server refused without processing.

A server retiring a connection sends GOAWAY naming the last stream it may have
processed. A request the client had already sent on a later stream was not
processed (RFC 9113 §6.8), and neither was a stream reset with REFUSED_STREAM
(§8.7), so both are safe to send again, and the transports do so when the
content can be replayed. Nothing else is resent.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio import to_thread
from h2.errors import ErrorCodes

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
from ._h2_server import H2TestServer, ServedConnection, ServedRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator


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


# ===== scripted server =====


def serve(connection: ServedConnection, request: ServedRequest) -> None:
    connection.respond(request.stream_id, b"served")


def refuse_first(
    refuse: Callable[[ServedConnection, ServedRequest], None],
) -> Callable[[ServedConnection, ServedRequest], None]:
    """A server that refuses the first request of its first connection with
    `refuse`, and serves every other one."""

    def on_request(connection: ServedConnection, request: ServedRequest) -> None:
        if connection.index == 0 and len(connection.requests) == 1:
            refuse(connection, request)
        else:
            serve(connection, request)

    return on_request


def reset_with(code: ErrorCodes) -> Callable[[ServedConnection, ServedRequest], None]:
    def reset(connection: ServedConnection, request: ServedRequest) -> None:
        connection.reset(request.stream_id, code)

    return reset


def reset_after_headers(connection: ServedConnection, request: ServedRequest) -> None:
    connection.send_headers(request.stream_id)
    connection.reset(request.stream_id, ErrorCodes.REFUSED_STREAM)


OTHER_RESETS = [
    pytest.param(ErrorCodes.CANCEL, id="cancel"),
    pytest.param(ErrorCodes.INTERNAL_ERROR, id="internal-error"),
]


def assert_refused(error: StreamError) -> None:
    assert error.code == StreamErrorCode.REFUSED_STREAM
    assert str(error).startswith("Request failed: ")


def assert_attempts(server: H2TestServer, path: str, connections: list[int]) -> None:
    """The request for `path` reached the server once per entry of
    `connections`, on the connections with those indexes, in that order. The
    server processed only the last attempt, which received the whole content.
    The server records a request at its headers and answers it at once, so the
    content may still be on its way when the client has the response."""
    attempts = server.requests(path)
    assert [index for index, _ in attempts] == connections
    served = attempts[-1][1]
    server.wait_until(lambda: served.ended, what="the served request's content")
    assert bytes(served.body) == PAYLOAD


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


@pytest.mark.anyio
async def test_async_refused_stream_is_resent() -> None:
    with H2TestServer(refuse_first(reset_with(ErrorCodes.REFUSED_STREAM))) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            response = await transport.execute(
                Request("POST", f"{server.url}/", content=PAYLOAD)
            )
            assert response.status == 200
            await aread(response.content)
            # The connection itself is fine, so the resend may reuse it.
            assert_attempts(server, "/", connections=[0, 0])


@pytest.mark.anyio
async def test_async_refused_stream_streamed_content_is_not_replayed() -> None:
    with H2TestServer(refuse_first(reset_with(ErrorCodes.REFUSED_STREAM))) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            with pytest.raises(StreamError) as info:
                await transport.execute(
                    Request("POST", f"{server.url}/", content=streamed())
                )
            assert_refused(info.value)
            await anyio.sleep(SETTLE)
            assert len(server.requests("/")) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("code", OTHER_RESETS)
async def test_async_other_reset_before_headers_is_not_resent(code: ErrorCodes) -> None:
    with H2TestServer(refuse_first(reset_with(code))) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            with pytest.raises(StreamError) as info:
                await transport.execute(
                    Request("POST", f"{server.url}/", content=PAYLOAD)
                )
            assert info.value.code == StreamErrorCode(int(code))
            await anyio.sleep(SETTLE)
            assert len(server.requests("/")) == 1


@pytest.mark.anyio
async def test_async_reset_after_headers_is_not_resent() -> None:
    with H2TestServer(refuse_first(reset_after_headers)) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            response = await transport.execute(
                Request("POST", f"{server.url}/", content=PAYLOAD)
            )
            assert response.status == 200
            with pytest.raises(StreamError) as info:
                await aread(response.content)
            assert info.value.code == StreamErrorCode.REFUSED_STREAM
            await anyio.sleep(SETTLE)
            assert len(server.requests("/")) == 1


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


def test_sync_refused_stream_is_resent() -> None:
    with (
        H2TestServer(refuse_first(reset_with(ErrorCodes.REFUSED_STREAM))) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        response = transport.execute_sync(
            SyncRequest("POST", f"{server.url}/", content=PAYLOAD)
        )
        assert response.status == 200
        read(response.content)
        assert_attempts(server, "/", connections=[0, 0])


def test_sync_refused_stream_streamed_content_is_not_replayed() -> None:
    with (
        H2TestServer(refuse_first(reset_with(ErrorCodes.REFUSED_STREAM))) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        with pytest.raises(StreamError) as info:
            transport.execute_sync(
                SyncRequest("POST", f"{server.url}/", content=streamed_sync())
            )
        assert_refused(info.value)
        time.sleep(SETTLE)
        assert len(server.requests("/")) == 1


@pytest.mark.parametrize("code", OTHER_RESETS)
def test_sync_other_reset_before_headers_is_not_resent(code: ErrorCodes) -> None:
    with (
        H2TestServer(refuse_first(reset_with(code))) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        with pytest.raises(StreamError) as info:
            transport.execute_sync(
                SyncRequest("POST", f"{server.url}/", content=PAYLOAD)
            )
        assert info.value.code == StreamErrorCode(int(code))
        time.sleep(SETTLE)
        assert len(server.requests("/")) == 1


def test_sync_reset_after_headers_is_not_resent() -> None:
    with (
        H2TestServer(refuse_first(reset_after_headers)) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        response = transport.execute_sync(
            SyncRequest("POST", f"{server.url}/", content=PAYLOAD)
        )
        assert response.status == 200
        with pytest.raises(StreamError) as info:
            read(response.content)
        assert info.value.code == StreamErrorCode.REFUSED_STREAM
        time.sleep(SETTLE)
        assert len(server.requests("/")) == 1

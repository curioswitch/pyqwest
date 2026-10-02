"""Requests whose HTTP/2 stream the server refused without processing.

A server retiring a connection sends GOAWAY naming the last stream it may have
processed. A request the client had already sent on a later stream was not
processed (RFC 9113 §6.8), and neither was a stream reset with REFUSED_STREAM
(§8.7), so both are safe to send again, and the transports do so when the
content can be replayed. Nothing else is resent.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import anyio
import pytest
from h2.errors import ErrorCodes

from pyqwest import (
    HTTPTransport,
    HTTPVersion,
    Request,
    StreamError,
    StreamErrorCode,
    SyncHTTPTransport,
    SyncRequest,
)

from ._h2_server import H2TestServer, ServedConnection, ServedRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Iterator


HELD_STREAM_ID = 1
"""The first stream a client opens on a connection."""

SETTLE = 0.3
"""How long a resend that must not happen is given to show up. A resend has no
backoff, so it would arrive in a few milliseconds."""

PAYLOAD = b"payload"


def served_on(connection: ServedConnection) -> bytes:
    return f"connection {connection.index}".encode()


def serve(connection: ServedConnection, request: ServedRequest) -> None:
    connection.respond(request.stream_id, served_on(connection))


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


def goaway(connection: ServedConnection, request: ServedRequest) -> None:
    """Retires the connection as if the request's stream arrived after the
    server stopped accepting streams: the GOAWAY names the stream before it,
    which for the first stream of a connection is none at all."""
    connection.goaway(max(request.stream_id - 2, 0))


def reset_with(code: ErrorCodes) -> Callable[[ServedConnection, ServedRequest], None]:
    def reset(connection: ServedConnection, request: ServedRequest) -> None:
        connection.reset(request.stream_id, code)

    return reset


def reset_after_headers(connection: ServedConnection, request: ServedRequest) -> None:
    connection.send_headers(request.stream_id)
    connection.reset(request.stream_id, ErrorCodes.REFUSED_STREAM)


def retiring_server(connection: ServedConnection, request: ServedRequest) -> None:
    """A server that holds the first stream of its first connection open with
    its headers sent, retires that connection with a GOAWAY naming the held
    stream when the next stream arrives, and serves everything else."""
    if connection.index > 0:
        serve(connection, request)
    elif request.stream_id == HELD_STREAM_ID:
        connection.send_headers(request.stream_id)
    else:
        connection.goaway(HELD_STREAM_ID)


def goaway_notice(connection: ServedConnection, request: ServedRequest) -> None:
    """A server that answers the first request of its first connection, then
    announces it will accept no more streams on it, the way grpc-go starts
    retiring a connection that reached its maximum age."""
    serve(connection, request)
    if connection.index == 0 and len(connection.requests) == 1:
        connection.goaway(2**31 - 1)


REFUSALS = [
    pytest.param(goaway, id="goaway"),
    pytest.param(reset_with(ErrorCodes.REFUSED_STREAM), id="refused-stream"),
]

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


# ===== async =====


async def aread(content: AsyncIterator[bytes | memoryview | bytearray]) -> bytes:
    return b"".join([chunk async for chunk in content])


async def streamed() -> AsyncIterator[bytes]:
    yield PAYLOAD


@pytest.mark.anyio
async def test_async_goaway_resends_on_new_connection() -> None:
    with H2TestServer(retiring_server) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            held = await transport.execute(Request("GET", f"{server.url}/held"))
            assert held.status == 200

            response = await transport.execute(
                Request("POST", f"{server.url}/racer", content=PAYLOAD)
            )
            assert response.status == 200
            assert await aread(response.content) == b"connection 1"

            # The refused stream went to the retiring connection, and the
            # resend to a new one, exactly once each.
            assert_attempts(server, "/racer", connections=[0, 1])

            # The stream the GOAWAY allowed runs to completion.
            server.connections[0].send_data(HELD_STREAM_ID, b"held", end_stream=True)
            assert await aread(held.content) == b"held"


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


@pytest.mark.anyio
@pytest.mark.parametrize("refuse", REFUSALS)
async def test_async_streamed_content_is_not_replayed(
    refuse: Callable[[ServedConnection, ServedRequest], None],
) -> None:
    with H2TestServer(refuse_first(refuse)) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            with pytest.raises(StreamError) as info:
                await transport.execute(
                    Request("POST", f"{server.url}/", content=streamed())
                )
            assert_refused(info.value)
            await anyio.sleep(SETTLE)
            assert len(server.requests("/")) == 1


@pytest.mark.anyio
async def test_async_request_after_goaway_notice_uses_new_connection() -> None:
    with H2TestServer(goaway_notice) as server:
        async with HTTPTransport(http_version=HTTPVersion.HTTP2) as transport:
            first = await transport.execute(Request("GET", f"{server.url}/first"))
            await aread(first.content)
            # The connection is still pooled when the notice arrives.
            await anyio.sleep(SETTLE)

            # Not even sent to the retiring connection, so streamed content is
            # fine: the request moves to a new connection untouched.
            response = await transport.execute(
                Request("POST", f"{server.url}/next", content=streamed())
            )
            assert await aread(response.content) == b"connection 1"
            assert_attempts(server, "/next", connections=[1])


# ===== sync =====


def read(content: Iterator[bytes | memoryview | bytearray]) -> bytes:
    return b"".join(content)


def streamed_sync() -> Iterator[bytes]:
    yield PAYLOAD


def test_sync_goaway_resends_on_new_connection() -> None:
    with (
        H2TestServer(retiring_server) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        held = transport.execute_sync(SyncRequest("GET", f"{server.url}/held"))
        assert held.status == 200

        response = transport.execute_sync(
            SyncRequest("POST", f"{server.url}/racer", content=PAYLOAD)
        )
        assert response.status == 200
        assert read(response.content) == b"connection 1"

        assert_attempts(server, "/racer", connections=[0, 1])

        server.connections[0].send_data(HELD_STREAM_ID, b"held", end_stream=True)
        assert read(held.content) == b"held"


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


@pytest.mark.parametrize("refuse", REFUSALS)
def test_sync_streamed_content_is_not_replayed(
    refuse: Callable[[ServedConnection, ServedRequest], None],
) -> None:
    with (
        H2TestServer(refuse_first(refuse)) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        with pytest.raises(StreamError) as info:
            transport.execute_sync(
                SyncRequest("POST", f"{server.url}/", content=streamed_sync())
            )
        assert_refused(info.value)
        time.sleep(SETTLE)
        assert len(server.requests("/")) == 1


def test_sync_request_after_goaway_notice_uses_new_connection() -> None:
    with (
        H2TestServer(goaway_notice) as server,
        SyncHTTPTransport(http_version=HTTPVersion.HTTP2) as transport,
    ):
        first = transport.execute_sync(SyncRequest("GET", f"{server.url}/first"))
        read(first.content)
        time.sleep(SETTLE)

        response = transport.execute_sync(
            SyncRequest("POST", f"{server.url}/next", content=streamed_sync())
        )
        assert read(response.content) == b"connection 1"
        assert_attempts(server, "/next", connections=[1])

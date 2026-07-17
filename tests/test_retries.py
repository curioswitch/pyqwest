from __future__ import annotations

import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest

from pyqwest import HTTPTransport, Request, SyncHTTPTransport, SyncRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve_one_request(port: int, delay: float) -> None:
    """Starts a server on the port after a delay and serves one request.

    Until the server starts, connection attempts to the port are refused,
    allowing tests to exercise connect retries.
    """
    time.sleep(delay)
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen()
        conn, _ = sock.accept()
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")


@pytest.mark.asyncio
async def test_async_retries_until_connect_succeeds() -> None:
    port = free_port()
    server = threading.Thread(target=serve_one_request, args=(port, 0.2))
    server.start()
    try:
        async with HTTPTransport(retries=3) as transport:
            res = await transport.execute(Request("GET", f"http://127.0.0.1:{port}/"))
            assert res.status == 200
            await res.aclose()
    finally:
        server.join()


def test_sync_retries_until_connect_succeeds() -> None:
    port = free_port()
    server = threading.Thread(target=serve_one_request, args=(port, 0.2))
    server.start()
    try:
        with SyncHTTPTransport(retries=3) as transport:
            res = transport.execute_sync(
                SyncRequest("GET", f"http://127.0.0.1:{port}/")
            )
            assert res.status == 200
            res.close()
    finally:
        server.join()


@pytest.mark.asyncio
async def test_async_retries_exhausted() -> None:
    port = free_port()
    async with HTTPTransport(retries=2) as transport:
        start = time.monotonic()
        with pytest.raises(ConnectionError):
            await transport.execute(Request("GET", f"http://127.0.0.1:{port}/"))
        # The second retry backs off for 0.5s.
        assert time.monotonic() - start >= 0.4


def test_sync_retries_exhausted() -> None:
    port = free_port()
    with SyncHTTPTransport(retries=2) as transport:
        start = time.monotonic()
        with pytest.raises(ConnectionError):
            transport.execute_sync(SyncRequest("GET", f"http://127.0.0.1:{port}/"))
        assert time.monotonic() - start >= 0.4


@pytest.mark.asyncio
async def test_async_streaming_request_not_retried() -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"

    port = free_port()
    async with HTTPTransport(retries=5) as transport:
        start = time.monotonic()
        with pytest.raises(ConnectionError):
            await transport.execute(
                Request("POST", f"http://127.0.0.1:{port}/", content=content())
            )
        # Retrying 5 times would back off for a total of 7.5s. A single
        # refused connect can itself take ~2s on Windows, which retransmits
        # before reporting the failure, so leave headroom below that.
        assert time.monotonic() - start < 5


@pytest.mark.asyncio
async def test_negative_retries() -> None:
    with pytest.raises(OverflowError):
        HTTPTransport(retries=-1)
    with pytest.raises(OverflowError):
        SyncHTTPTransport(retries=-1)

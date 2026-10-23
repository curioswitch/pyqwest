from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest
from anyio import to_thread
from anyio.streams.buffered import BufferedByteReceiveStream

from pyqwest import Client, HTTPTransport, HTTPVersion, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.abc import SocketStream

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix domain sockets are only supported on Unix"
)

# The target host does not resolve, so a successful response can only
# have been served over the socket.
TARGET_URL = "http://pyqwest.invalid/echo"


@dataclass
class RecordingServer:
    path: Path
    requests: list[bytes] = field(default_factory=list)

    def request_line(self) -> bytes:
        return self.requests[0].split(b"\r\n")[0]

    def request_headers(self) -> dict[bytes, bytes]:
        headers = {}
        for line in self.requests[0].split(b"\r\n")[1:]:
            if not line:
                continue
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip()
        return headers


@pytest.fixture
async def server() -> AsyncIterator[RecordingServer]:
    recorded: list[bytes] = []

    async def handle(stream: SocketStream) -> None:
        async with stream:
            buffered = BufferedByteReceiveStream(stream)
            head = await buffered.receive_until(b"\r\n\r\n", 65536)
            recorded.append(head + b"\r\n\r\n")
            await stream.send(
                b"HTTP/1.1 200 OK\r\ncontent-length: 3\r\nconnection: close\r\n\r\nuds"
            )

    # pytest's tmp_path can exceed the socket path limit of macOS.
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "server.sock"
        listener = await anyio.create_unix_listener(path)
        async with listener, anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            yield RecordingServer(path=path, requests=recorded)
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_uds(server: RecordingServer) -> None:
    async with HTTPTransport(uds=server.path, timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"uds"
    assert server.request_line() == b"GET /echo HTTP/1.1"
    assert server.request_headers()[b"host"] == b"pyqwest.invalid"


@pytest.mark.anyio
async def test_uds_sync(server: RecordingServer) -> None:
    with SyncHTTPTransport(uds=server.path, timeout=10) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, TARGET_URL)
    assert res.status == 200
    assert res.content == b"uds"
    assert server.request_line() == b"GET /echo HTTP/1.1"
    assert server.request_headers()[b"host"] == b"pyqwest.invalid"


@pytest.mark.anyio
async def test_uds_str_path(server: RecordingServer) -> None:
    async with HTTPTransport(uds=str(server.path), timeout=10) as transport:
        res = await Client(transport).get(TARGET_URL)
    assert res.status == 200
    assert res.content == b"uds"


@pytest.mark.anyio
async def test_uds_missing_socket() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "missing.sock"
        async with HTTPTransport(uds=path, timeout=10) as transport:
            with pytest.raises(ConnectionError):
                await Client(transport).get(TARGET_URL)


def test_uds_http3() -> None:
    with pytest.raises(ValueError, match="uds cannot be used with HTTP/3"):
        HTTPTransport(uds="/tmp/server.sock", http_version=HTTPVersion.HTTP3)  # noqa: S108
    with pytest.raises(ValueError, match="uds cannot be used with HTTP/3"):
        SyncHTTPTransport(uds="/tmp/server.sock", http_version=HTTPVersion.HTTP3)  # noqa: S108


def test_uds_proxy() -> None:
    with pytest.raises(ValueError, match="uds cannot be used with proxy"):
        HTTPTransport(uds="/tmp/server.sock", proxy="http://localhost:8030")  # noqa: S108
    with pytest.raises(ValueError, match="uds cannot be used with proxy"):
        SyncHTTPTransport(uds="/tmp/server.sock", proxy="http://localhost:8030")  # noqa: S108

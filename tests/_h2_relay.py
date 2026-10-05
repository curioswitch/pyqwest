"""A TCP relay that lets a test delay HTTP/2 traffic to and from a real server.

The relay forwards bytes unmodified in both directions. On the first connection
it accepts, a test can hold the server's GOAWAY frames back from the client,
and hold the client's bytes back from the server, then release each when it
chooses. That reproduces, deterministically, a request whose HEADERS are in
flight when the server sends GOAWAY, which on loopback is a window of one round
trip. Later connections pass straight through.
"""

from __future__ import annotations

import contextlib
import socket
import struct
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

WAIT_TIMEOUT = 5.0

_FRAME_HEADER_LEN = 9
_GOAWAY_FRAME_TYPE = 0x7


class Relay:
    """Relays connections made to `port` to `upstream_port` on the loopback
    interface."""

    def __init__(self, upstream_port: int) -> None:
        self.upstream_port = upstream_port
        self.connections = 0
        """Connections accepted so far."""
        self.goaways: list[tuple[int, int]] = []
        """The (last stream ID, error code) of each GOAWAY the server sent on
        the first connection, held or not."""
        self._cond = threading.Condition()
        self._hold_goaways = False
        self._held_goaways: list[bytes] = []
        self._hold_client = False
        self._held_client = bytearray()
        self._server_eof = False
        self._first_client: socket.socket | None = None
        self._first_server: socket.socket | None = None
        self._sockets: list[socket.socket] = []
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._threads: list[threading.Thread] = []

    @property
    def port(self) -> int:
        return self._listener.getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> Relay:
        self._spawn(self._accept)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        with self._cond:
            sockets = [self._listener, *self._sockets]
            self._sockets.clear()
        for sock in sockets:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()
        for thread in self._threads:
            thread.join(timeout=WAIT_TIMEOUT)

    def hold_goaways(self) -> None:
        """Keeps GOAWAY frames from the server on the first connection from
        reaching the client until `release_goaways`. Other frames pass."""
        with self._cond:
            self._hold_goaways = True

    def hold_client(self) -> None:
        """Keeps everything the client sends on the first connection from
        reaching the server until `release_client`."""
        with self._cond:
            self._hold_client = True

    def wait_for_goaways(self, n: int) -> None:
        """Waits until the server has sent `n` GOAWAY frames on the first
        connection."""
        self._wait(lambda: len(self.goaways) >= n, what=f"{n} GOAWAY frames")

    def wait_for_held_client(self) -> None:
        """Waits until the client has sent something that is being held."""
        self._wait(lambda: len(self._held_client) > 0, what="held client bytes")

    def release_goaways(self, count: int | None = None) -> None:
        """Delivers the held GOAWAY frames to the client, the first `count` of
        them if given, in order. Once none are held, GOAWAY frames pass again."""
        with self._cond:
            if count is None:
                count = len(self._held_goaways)
            frames = b"".join(self._held_goaways[:count])
            del self._held_goaways[:count]
            if not self._held_goaways:
                self._hold_goaways = False
            close = self._server_eof and not self._held_goaways
            client = self._first_client
        if client is None:
            msg = "no connection has been accepted"
            raise RuntimeError(msg)
        if frames:
            client.sendall(frames)
        if close:
            with contextlib.suppress(OSError):
                client.shutdown(socket.SHUT_WR)

    def release_client(self) -> None:
        """Delivers the held client bytes to the server. Later bytes pass."""
        with self._cond:
            data = bytes(self._held_client)
            self._held_client.clear()
            self._hold_client = False
            server = self._first_server
        if server is None:
            msg = "no connection has been accepted"
            raise RuntimeError(msg)
        if data:
            server.sendall(data)

    def _wait(self, predicate: Callable[[], bool], *, what: str) -> None:
        with self._cond:
            if not self._cond.wait_for(predicate, WAIT_TIMEOUT):
                msg = f"timed out waiting for {what}"
                raise TimeoutError(msg)

    def _spawn(
        self, target: Callable[..., None], *args: object, **kwargs: object
    ) -> None:
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            server = socket.create_connection(("127.0.0.1", self.upstream_port))
            with self._cond:
                self.connections += 1
                first = self.connections == 1
                if first:
                    self._first_client, self._first_server = client, server
                self._sockets += [client, server]
            self._spawn(self._client_to_server, client, server, first=first)
            self._spawn(self._server_to_client, client, server, first=first)

    def _client_to_server(
        self, client: socket.socket, server: socket.socket, *, first: bool
    ) -> None:
        while True:
            try:
                data = client.recv(65536)
            except OSError:
                data = b""
            if not data:
                with contextlib.suppress(OSError):
                    server.shutdown(socket.SHUT_WR)
                return
            with self._cond:
                if first and self._hold_client:
                    self._held_client += data
                    self._cond.notify_all()
                    continue
            try:
                server.sendall(data)
            except OSError:
                return

    def _server_to_client(
        self, client: socket.socket, server: socket.socket, *, first: bool
    ) -> None:
        buffer = bytearray()
        while True:
            try:
                data = server.recv(65536)
            except OSError:
                data = b""
            if not data:
                with self._cond:
                    if first:
                        self._server_eof = True
                        self._cond.notify_all()
                        if self._held_goaways:
                            # release_goaways closes the client side after
                            # delivering them.
                            return
                with contextlib.suppress(OSError):
                    client.shutdown(socket.SHUT_WR)
                return
            if not first:
                client.sendall(data)
                continue
            buffer += data
            passing = bytearray()
            while len(buffer) >= _FRAME_HEADER_LEN:
                length = int.from_bytes(buffer[:3], "big")
                if len(buffer) < _FRAME_HEADER_LEN + length:
                    break
                frame = bytes(buffer[: _FRAME_HEADER_LEN + length])
                del buffer[: _FRAME_HEADER_LEN + length]
                if frame[3] == _GOAWAY_FRAME_TYPE:
                    last_stream_id, error_code = struct.unpack("!II", frame[9:17])
                    with self._cond:
                        self.goaways.append((last_stream_id & 0x7FFFFFFF, error_code))
                        self._cond.notify_all()
                        if self._hold_goaways:
                            if passing:
                                client.sendall(bytes(passing))
                                passing = bytearray()
                            self._held_goaways.append(frame)
                            continue
                passing += frame
            if passing:
                client.sendall(bytes(passing))

"""A plaintext HTTP/2 server that keeps response streams open on request.

It advertises a small `SETTINGS_MAX_CONCURRENT_STREAMS`, counts the connections
and streams it sees, and holds `/stream` responses open after their headers
until the test finishes them, which is the shape of a server-sent-events or
RPC server-stream endpoint behind a proxy with a stream limit.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from dataclasses import dataclass, field
from queue import Queue
from typing import TYPE_CHECKING

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.errors import ErrorCodes
from h2.events import ConnectionTerminated, DataReceived, RequestReceived, StreamReset
from h2.exceptions import ProtocolError
from h2.settings import SettingCodes, Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

RECV_TIMEOUT = 0.02
WAIT_TIMEOUT = 10.0


@dataclass
class _Connection:
    """One accepted TCP connection and the commands waiting to run on it."""

    sock: socket.socket
    commands: Queue[tuple[str, int]] = field(default_factory=Queue)


@dataclass(frozen=True)
class OpenStream:
    """A `/stream` response held open, identified by its connection and stream id."""

    connection: int
    stream_id: int


class H2Server:
    """Serves `/stream` responses held open until finished, and short complete
    responses for any other path.

    Counts are updated under `_cond`, which `wait_for` polls on.
    """

    def __init__(self, max_concurrent_streams: int) -> None:
        self.max_concurrent_streams = max_concurrent_streams
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(16)
        self.port: int = self._listener.getsockname()[1]
        self._cond = threading.Condition()
        self._connections: list[_Connection] = []
        self._threads: list[threading.Thread] = []
        self._closed = False
        # Counters, all under _cond.
        self.connections = 0
        """TCP connections accepted."""
        self.streams_received = 0
        """Request streams received, on any connection."""
        self.streams_reset = 0
        """Streams the client reset before the server finished them."""
        self.open_streams: list[OpenStream] = []
        """`/stream` responses currently held open, oldest first."""
        self._accept_thread = threading.Thread(target=self._accept, daemon=True)
        self._accept_thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def wait_for(self, attr: str, value: int, *, timeout: float = WAIT_TIMEOUT) -> None:
        """Waits until the counter `attr` reaches `value`."""
        with self._cond:
            if not self._cond.wait_for(
                lambda: getattr(self, attr) >= value, timeout=timeout
            ):
                msg = f"{attr} is {getattr(self, attr)}, expected {value}"
                raise TimeoutError(msg)

    def wait_for_open_streams(
        self, count: int, *, timeout: float = WAIT_TIMEOUT
    ) -> None:
        """Waits until exactly `count` `/stream` responses are held open."""
        with self._cond:
            if not self._cond.wait_for(
                lambda: len(self.open_streams) == count, timeout=timeout
            ):
                msg = f"{len(self.open_streams)} open streams, expected {count}"
                raise TimeoutError(msg)

    def finish_stream(self, stream: OpenStream | None = None) -> None:
        """Ends `stream`, or the oldest open one, with a final data frame."""
        self._command("finish", stream)

    def reset_stream(self, stream: OpenStream | None = None) -> None:
        """Resets `stream`, or the oldest open one, so the client's read fails."""
        self._command("reset", stream)

    def finish_all(self) -> None:
        with self._cond:
            streams = list(self.open_streams)
        for stream in streams:
            self.finish_stream(stream)

    def _command(self, command: str, stream: OpenStream | None) -> None:
        with self._cond:
            if stream is None:
                if not self.open_streams:
                    msg = "no open stream"
                    raise LookupError(msg)
                stream = self.open_streams[0]
            self.open_streams.remove(stream)
            connection = self._connections[stream.connection]
        connection.commands.put((command, stream.stream_id))

    def close(self) -> None:
        with self._cond:
            self._closed = True
            connections = list(self._connections)
        self._listener.close()
        for connection in connections:
            with contextlib.suppress(OSError):
                connection.sock.shutdown(socket.SHUT_RDWR)
            connection.sock.close()
        for thread in self._threads:
            thread.join(timeout=5)

    def _accept(self) -> None:
        while True:
            try:
                sock, _ = self._listener.accept()
            except OSError:
                return
            with self._cond:
                if self._closed:
                    sock.close()
                    return
                connection = _Connection(sock)
                index = len(self._connections)
                self._connections.append(connection)
                self.connections += 1
                self._cond.notify_all()
                thread = threading.Thread(
                    target=self._serve, args=(connection, index), daemon=True
                )
                self._threads.append(thread)
            thread.start()

    def _serve(self, connection: _Connection, index: int) -> None:
        sock = connection.sock
        conn = H2Connection(
            config=H2Configuration(client_side=False, header_encoding="utf-8")
        )
        conn.local_settings = Settings(
            client=False,
            initial_values={
                SettingCodes.MAX_CONCURRENT_STREAMS: self.max_concurrent_streams
            },
        )
        conn.initiate_connection()
        sock.sendall(conn.data_to_send())
        sock.settimeout(RECV_TIMEOUT)
        with contextlib.suppress(OSError, ProtocolError):
            while True:
                try:
                    data = sock.recv(65536)
                except TimeoutError:
                    data = None
                if data == b"":
                    break
                terminated = False
                if data:
                    for event in conn.receive_data(data):
                        if isinstance(event, RequestReceived):
                            self._on_request(conn, index, event)
                        elif isinstance(event, DataReceived):
                            conn.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamReset):
                            self._on_reset(index, event.stream_id)
                        elif isinstance(event, ConnectionTerminated):
                            terminated = True
                for command, stream_id in _drain(connection.commands):
                    with contextlib.suppress(ProtocolError):
                        if command == "finish":
                            conn.send_data(stream_id, b"done", end_stream=True)
                        else:
                            conn.reset_stream(stream_id, ErrorCodes.INTERNAL_ERROR)
                out = conn.data_to_send()
                if out:
                    sock.sendall(out)
                if terminated:
                    break

    def _on_request(
        self, conn: H2Connection, index: int, event: RequestReceived
    ) -> None:
        assert event.stream_id is not None  # noqa: S101
        headers = dict(event.headers or [])
        conn.send_headers(
            event.stream_id, [(":status", "200"), ("content-type", "text/plain")]
        )
        with self._cond:
            self.streams_received += 1
            if headers.get(":path") == "/stream":
                self.open_streams.append(OpenStream(index, event.stream_id))
            self._cond.notify_all()
        if headers.get(":path") != "/stream":
            conn.send_data(event.stream_id, b"hello", end_stream=True)

    def _on_reset(self, index: int, stream_id: int) -> None:
        with self._cond:
            stream = OpenStream(index, stream_id)
            if stream in self.open_streams:
                self.open_streams.remove(stream)
            self.streams_reset += 1
            self._cond.notify_all()


def _drain(queue: Queue[tuple[str, int]]) -> Iterator[tuple[str, int]]:
    """Yields the queued commands. The serving thread is the only consumer."""
    while not queue.empty():
        yield queue.get_nowait()


@contextlib.contextmanager
def h2_server(max_concurrent_streams: int) -> Iterator[H2Server]:
    server = H2Server(max_concurrent_streams)
    try:
        yield server
    finally:
        server.close()

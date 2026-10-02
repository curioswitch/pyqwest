"""A scripted plaintext HTTP/2 server for tests of how streams are refused.

Each test decides what the server does with a request, frame by frame, so a
test names exactly the fault it exercises: a GOAWAY that leaves a stream
unprocessed, a RST_STREAM before or after the response headers, and so on.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import h2.config
import h2.connection
import h2.errors
import h2.events
import h2.exceptions
from hyperframe.frame import GoAwayFrame

if TYPE_CHECKING:
    from collections.abc import Callable

WAIT_TIMEOUT = 5.0

# Recv wakes this often to notice the server stopping. Stopping also shuts the
# socket down, so this is only a safety net.
READ_TIMEOUT = 0.5


@dataclass
class ServedRequest:
    """A request stream as the server saw it."""

    stream_id: int
    headers: dict[str, str]
    body: bytearray = field(default_factory=bytearray)
    ended: bool = False

    @property
    def path(self) -> str:
        return self.headers[":path"]


class ServedConnection:
    """One accepted connection, driven by its own thread.

    Frames are sent under a lock, so a test thread may finish or reset a
    stream while the connection thread keeps reading.
    """

    def __init__(self, index: int, sock: socket.socket, server: H2TestServer) -> None:
        self.index = index
        self.requests: list[ServedRequest] = []
        self.closed = False
        self._sock = sock
        self._server = server
        self._h2 = h2.connection.H2Connection(
            h2.config.H2Configuration(client_side=False, header_encoding="utf-8")
        )
        self._lock = threading.RLock()

    def request(self, stream_id: int) -> ServedRequest:
        return next(r for r in self.requests if r.stream_id == stream_id)

    def respond(self, stream_id: int, body: bytes = b"", *, status: int = 200) -> None:
        """Sends a complete response."""
        with self._lock:
            self._h2.send_headers(
                stream_id,
                [(":status", str(status)), ("content-length", str(len(body)))],
                end_stream=not body,
            )
            if body:
                self._h2.send_data(stream_id, body, end_stream=True)
            self._flush()

    def send_headers(self, stream_id: int, *, status: int = 200) -> None:
        """Sends response headers, leaving the stream open for the body."""
        with self._lock:
            self._h2.send_headers(stream_id, [(":status", str(status))])
            self._flush()

    def send_data(self, stream_id: int, data: bytes, *, end_stream: bool) -> None:
        with self._lock:
            self._h2.send_data(stream_id, data, end_stream=end_stream)
            self._flush()

    def reset(self, stream_id: int, code: h2.errors.ErrorCodes) -> None:
        with self._lock:
            self._h2.reset_stream(stream_id, error_code=code)
            self._flush()

    def goaway(
        self,
        last_stream_id: int,
        *,
        code: h2.errors.ErrorCodes = h2.errors.ErrorCodes.NO_ERROR,
        debug_data: bytes = b"max_age",
    ) -> None:
        """Sends GOAWAY as a raw frame. The h2 state machine stays open, so
        streams at or below `last_stream_id` can still be served afterwards,
        which `close_connection` would forbid."""
        frame = GoAwayFrame(
            stream_id=0,
            last_stream_id=last_stream_id,
            error_code=int(code),
            additional_data=debug_data,
        )
        with self._lock:
            self._sock.sendall(frame.serialize())

    def close(self) -> None:
        with self._lock:
            self.closed = True
            with contextlib.suppress(OSError):
                self._sock.shutdown(socket.SHUT_RDWR)
            self._sock.close()

    def serve(self) -> None:
        with self._lock:
            self._h2.initiate_connection()
            self._flush()
        self._sock.settimeout(READ_TIMEOUT)
        try:
            while not self._server.stopping:
                try:
                    data = self._sock.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                with self._lock:
                    try:
                        events = self._h2.receive_data(data)
                    except h2.exceptions.ProtocolError:
                        break
                    for event in events:
                        if isinstance(event, h2.events.ConnectionTerminated):
                            return
                        self._handle(event)
                    self._flush()
        finally:
            self.close()
            self._server.notify()

    def _handle(self, event: h2.events.Event) -> None:
        if isinstance(event, h2.events.RequestReceived):
            assert event.stream_id is not None  # noqa: S101
            request = ServedRequest(
                event.stream_id,
                {str(name): str(value) for name, value in event.headers or []},
            )
            self.requests.append(request)
            self._server.notify()
            self._server.on_request(self, request)
        elif isinstance(event, h2.events.DataReceived):
            assert event.stream_id is not None  # noqa: S101
            self.request(event.stream_id).body.extend(event.data or b"")
            self._h2.acknowledge_received_data(
                event.flow_controlled_length or 0, event.stream_id
            )
        elif isinstance(event, h2.events.StreamEnded):
            assert event.stream_id is not None  # noqa: S101
            self.request(event.stream_id).ended = True
            self._server.notify()

    def _flush(self) -> None:
        data = self._h2.data_to_send()
        if data and not self.closed:
            with contextlib.suppress(OSError):
                self._sock.sendall(data)


class H2TestServer:
    """Accepts plaintext HTTP/2 connections, calling `on_request` with each
    request stream so the test decides how it is answered. Usable as a context
    manager, and from any thread."""

    def __init__(
        self, on_request: Callable[[ServedConnection, ServedRequest], None]
    ) -> None:
        self.on_request = on_request
        self.connections: list[ServedConnection] = []
        self.stopping = False
        self._cond = threading.Condition()
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        self._listener.settimeout(READ_TIMEOUT)
        self._threads: list[threading.Thread] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._listener.getsockname()[1]}"

    def __enter__(self) -> H2TestServer:
        thread = threading.Thread(target=self._accept, daemon=True)
        thread.start()
        self._threads.append(thread)
        return self

    def __exit__(self, *_: object) -> None:
        self.stopping = True
        for connection in list(self.connections):
            connection.close()
        self._listener.close()
        for thread in self._threads:
            thread.join(timeout=WAIT_TIMEOUT)

    def requests(self, path: str | None = None) -> list[tuple[int, ServedRequest]]:
        """Every request seen so far as (connection index, request), oldest
        first, optionally only those for `path`."""
        return [
            (connection.index, request)
            for connection in list(self.connections)
            for request in list(connection.requests)
            if path is None or request.path == path
        ]

    def wait_for_requests(self, n: int, path: str | None = None) -> None:
        """Waits until the server has seen `n` requests, for `path` if given."""
        self.wait_until(lambda: len(self.requests(path)) >= n, what=f"{n} requests")

    def wait_until(self, condition: Callable[[], bool], *, what: str) -> None:
        deadline = time.monotonic() + WAIT_TIMEOUT
        with self._cond:
            while not condition():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    msg = f"timed out waiting for {what}"
                    raise TimeoutError(msg)
                self._cond.wait(remaining)

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _accept(self) -> None:
        while not self.stopping:
            try:
                sock, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            connection = ServedConnection(len(self.connections), sock, self)
            self.connections.append(connection)
            self.notify()
            thread = threading.Thread(target=connection.serve, daemon=True)
            thread.start()
            self._threads.append(thread)

from __future__ import annotations

import contextlib
import socket
import struct
import threading
from collections.abc import Iterator
from queue import Empty, Queue


@contextlib.contextmanager
def raw_server(response: bytes, *, reset: bool = False) -> Iterator[str]:
    """Serves `response` verbatim to a single request, then closes the connection.

    Passing `reset` closes with an RST rather than a FIN, which is the
    difference between a truncated response being a protocol violation and
    being a broken connection.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve() -> None:
        with listener, contextlib.closing(listener.accept()[0]) as conn:
            conn.recv(65536)
            conn.sendall(response)
            if reset:
                # SO_LINGER with a zero timeout makes close() send RST rather
                # than FIN, so the peer sees a connection reset.
                conn.setsockopt(
                    socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
                )

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        thread.join(timeout=5)


# Responses that violate HTTP framing in some way. Each is served by
# `raw_server`, followed by a clean FIN.
NO_RESPONSE = b""
"""The server accepts the request then disconnects without responding."""

MALFORMED_STATUS_LINE = b"HTTP/1.1 boom\r\n\r\n"
"""The status line names a status that is not a number."""

GARBAGE_RESPONSE = b"this is not http\r\n\r\n"
"""Not an HTTP response at all."""

BAD_CHUNKED_FRAMING = (
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nZZZZ\r\nhello\r\n"
)
"""The chunk size is not hexadecimal."""

TRUNCATED_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\npartial"
"""Promises more body than is sent, so the close truncates the response."""

TRUNCATED_CHUNKED_RESPONSE = (
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n"
)
"""Ends without the terminating zero-length chunk."""


class SyncRequestBody(Iterator[bytes]):
    _queue: Queue[bytes | None]

    def __init__(self) -> None:
        self._queue = Queue()
        self._closed = False
        self._pending_read = False

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        while True:
            self._pending_read = True
            try:
                item = self._queue.get(timeout=0.01)
                break
            except Empty:
                if self._closed:
                    item = None
                    break
        self._pending_read = False

        if item is None:
            raise StopIteration
        return item

    def put(self, item: bytes) -> None:
        self._queue.put(item)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)

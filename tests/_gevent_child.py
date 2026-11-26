"""Runs sync requests under gevent for `test_gevent`.

Usage: _gevent_child.py URL HTTP_VERSION

Requests go to the test server's `/echo` endpoint, which sends back each
request body chunk as it arrives. If the request body pauses, so does the
response. Prints `ok` if every check passes.
"""

from __future__ import annotations

from gevent import monkey

monkey.patch_all()

import sys  # noqa: E402
import time  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

import gevent  # noqa: E402

from pyqwest import HTTPVersion, SyncClient, SyncHTTPTransport  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator

# Fail instead of hanging if a request never finishes. gevent can't interrupt
# a wait that blocks the hub, so run_child's own timeout covers that case.
RUN_TIMEOUT = 30

HTTP_VERSIONS = {"h1": HTTPVersion.HTTP1, "h2": HTTPVersion.HTTP2}


class PausingBody:
    """Request body that yields each `bytes` item and sleeps for each `float`
    item, in seconds.

    pyqwest reads request bodies on a native thread, where the patched
    `time.sleep` uses that thread's own hub. `close()` cuts a pause short.
    """

    def __init__(self, *items: bytes | float) -> None:
        self._items = iter(items)
        self._closed = False

    def __iter__(self) -> PausingBody:
        return self

    def __next__(self) -> bytes:
        for item in self._items:
            if isinstance(item, bytes):
                return item
            deadline = time.monotonic() + item
            while not self._closed and time.monotonic() < deadline:
                time.sleep(0.01)
        raise StopIteration

    def close(self) -> None:
        self._closed = True


def next_data(chunks: Iterator[bytes | memoryview | bytearray]) -> bytes:
    """Returns the next chunk that is not empty."""
    while not (chunk := bytes(next(chunks))):
        pass
    return chunk


def expect(actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"expected {expected!r}, got {actual!r}"
        raise AssertionError(msg)


def main(url: str, http_version: HTTPVersion) -> None:
    echo = f"{url}/echo"
    with SyncHTTPTransport(http_version=http_version) as transport:
        client = SyncClient(transport)

        expect(client.post(echo, content=b"hello").content, b"hello")

        # The ticker can only run during the request if the request's waits
        # yield to the hub.
        ticks: list[None] = []

        def tick() -> None:
            while True:
                gevent.sleep(0.01)
                ticks.append(None)

        ticker = gevent.spawn(tick)
        with client.stream("POST", echo, content=PausingBody(b"a", 0.2, b"b")) as res:
            expect(b"".join(bytes(chunk) for chunk in res.content), b"ab")
        ticked = len(ticks) > 0
        ticker.kill()
        expect(ticked, True)  # noqa: FBT003

        # Concurrent requests from many greenlets.
        jobs = [
            gevent.spawn(client.post, echo, content=PausingBody(b"a", 0.05, b"b"))
            for _ in range(20)
        ]
        gevent.joinall(jobs, raise_error=True)
        expect([job.value.content for job in jobs], [b"ab"] * 20)

        # A timeout interrupts a request, and the client still works after.
        interrupted = False
        try:
            with gevent.Timeout(0.1):
                client.post(echo, content=PausingBody(b"a", RUN_TIMEOUT))
        except gevent.Timeout:
            interrupted = True
        expect(interrupted, True)  # noqa: FBT003
        expect(client.post(echo, content=b"hello").content, b"hello")

        # A timeout during a chunk read doesn't lose the rest of the body.
        body = PausingBody(b"a", 0.5, b"b", b"c")
        with client.stream("POST", echo, content=body) as res:
            chunks = iter(res.content)
            expect(next_data(chunks), b"a")
            interrupted = False
            try:
                with gevent.Timeout(0.1):
                    next(chunks)
            except gevent.Timeout:
                interrupted = True
            expect(interrupted, True)  # noqa: FBT003
            expect(b"".join(bytes(chunk) for chunk in chunks), b"bc")

    print("ok")  # noqa: T201


if __name__ == "__main__":
    with gevent.Timeout(RUN_TIMEOUT):
        main(sys.argv[1], HTTP_VERSIONS[sys.argv[2]])

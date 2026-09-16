"""Frees a response while an exception propagates, for `test_unwinding`.

Run with the library (`asyncio` or `trio`) as the argument. Prints "ok" if the
interpreter survives, the response's `Drop` cancelled the task streaming its
request body, and nothing was reported as unraisable.
"""

from __future__ import annotations

import asyncio
import gc
import http.server
import sys
import threading
from typing import TYPE_CHECKING, NoReturn

import trio

from pyqwest import HTTPTransport, Request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LIBRARIES = ("asyncio", "trio")

unraisables: list[str] = []


def record_unraisable(unraisable: sys.UnraisableHookArgs) -> None:
    unraisables.append(f"{unraisable.exc_type.__name__}: {unraisable.exc_value}")


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()
        threading.Event().wait()  # hold the connection without reading the body


def new_event(library: str) -> asyncio.Event | trio.Event:
    return trio.Event() if library == "trio" else asyncio.Event()


async def sleep(library: str, seconds: float) -> None:
    if library == "trio":
        await trio.sleep(seconds)
    else:
        await asyncio.sleep(seconds)


async def wait_briefly(library: str, event: asyncio.Event | trio.Event) -> None:
    """Waits for `event`, failing after 5 seconds."""
    if library == "trio":
        with trio.fail_after(5):
            await event.wait()
    else:
        await asyncio.wait_for(event.wait(), timeout=5)


def boom() -> NoReturn:
    msg = "boom"
    raise ValueError(msg)


def pair(_first: object, _second: object) -> None: ...


async def main(library: str, url: str) -> None:
    started, closed = new_event(library), new_event(library)

    async def body() -> AsyncIterator[bytes]:
        started.set()
        try:
            yield b"x"
            await new_event(library).wait()
        finally:
            closed.set()

    async with HTTPTransport() as transport:
        responses = [await transport.execute(Request("POST", url, content=body()))]
        await wait_briefly(library, started)
        # The response can stay referenced after the await: a tokio thread
        # releases its reference as a decref that pyo3 defers until its next
        # call into Python. `gc` sees neither pending decrefs nor Rust owners,
        # so compare reference counts with an object only a list holds.
        probe = [object()]
        extra = -1
        for _ in range(100):
            await sleep(library, 0.01)
            # Reading `status` is a pyo3 call, so it applies deferred decrefs.
            assert responses[0].status == 200  # noqa: S101
            extra = sys.getrefcount(responses[0]) - sys.getrefcount(probe[0])
            if not extra:
                break
        owners = [type(r).__name__ for r in gc.get_referrers(responses[0])]
        assert not extra, (extra, owners)  # noqa: S101
        assert not closed.is_set()  # noqa: S101
        try:
            # pop() leaves the call's arguments as the response's only owner.
            pair(responses.pop(), boom())
        except ValueError:
            # The Drop cancelled the body task.
            await wait_briefly(library, closed)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in LIBRARIES:
        sys.exit(f"usage: {sys.argv[0]} {{{'|'.join(LIBRARIES)}}}")
    library = sys.argv[1]
    sys.unraisablehook = record_unraisable
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if library == "trio":
        trio.run(main, library, url)
    else:
        asyncio.run(main(library, url))
    assert not unraisables, unraisables  # noqa: S101
    print("ok", flush=True)  # noqa: T201

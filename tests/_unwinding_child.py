"""Frees a response while an exception propagates, for `test_unwinding`.

Prints "ok" if the interpreter survives, the response's `Drop` cancelled the
task streaming its request body, and nothing was reported as unraisable.
"""

from __future__ import annotations

import asyncio
import gc
import http.server
import sys
import threading
from typing import TYPE_CHECKING, NoReturn

from pyqwest import HTTPTransport, Request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

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


def boom() -> NoReturn:
    msg = "boom"
    raise ValueError(msg)


def pair(_first: object, _second: object) -> None: ...


async def main(url: str) -> None:
    started, closed = asyncio.Event(), asyncio.Event()

    async def body() -> AsyncIterator[bytes]:
        started.set()
        try:
            yield b"x"
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with HTTPTransport() as transport:
        responses = [await transport.execute(Request("POST", url, content=body()))]
        await asyncio.wait_for(started.wait(), timeout=5)
        # The finished future holding the response can outlive the await, and a
        # tokio thread releases its reference as a decref that pyo3 defers until
        # its next call into Python. `gc` sees neither pending decrefs nor Rust
        # owners, so compare reference counts with an object only a list holds.
        probe = [object()]
        extra = -1
        for _ in range(100):
            await asyncio.sleep(0.01)
            # Reading `status` is a pyo3 call, so it applies the deferred decrefs.
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
            await asyncio.wait_for(closed.wait(), timeout=5)


if __name__ == "__main__":
    sys.unraisablehook = record_unraisable
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    asyncio.run(main(f"http://127.0.0.1:{server.server_address[1]}/"))
    assert not unraisables, unraisables  # noqa: S101
    print("ok", flush=True)  # noqa: T201

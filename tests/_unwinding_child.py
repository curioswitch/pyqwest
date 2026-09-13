"""Frees a response or an unawaited call while an exception propagates.

Run by `test_unwinding` with the library (`asyncio` or `trio`) and the case as
arguments. Prints "ok" if the interpreter survives and nothing was reported as
unraisable. The response cases first check that the response's `Drop`
cancelled the task streaming its request body.
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

    from pyqwest import Response

# Each case frees an object whose Drop calls into Python:
# - unawaited: an unawaited trio `execute()` call; its done callback's Drop ends
#   the operation.
# - unawaited-streamed-body: the same with a streamed request body.
# - response: a response; its Drop cancels the task streaming its request body.
# asyncio has no unawaited case: its request starts at once and tokio holds the
# future, so freeing the call while an exception propagates runs no Drop.
CASES = {
    ("trio", "unawaited"),
    ("trio", "unawaited-streamed-body"),
    ("trio", "response"),
    ("asyncio", "response"),
}

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


async def main(library: str, case: str, url: str) -> None:
    started, closed = new_event(library), new_event(library)

    async def body() -> AsyncIterator[bytes]:
        started.set()
        try:
            yield b"x"
            await new_event(library).wait()
        finally:
            closed.set()

    async with HTTPTransport() as transport:
        request = Request("POST", url, content=None if case == "unawaited" else body())
        responses: list[Response] = []
        if case == "response":
            responses.append(await transport.execute(request))
            await wait_briefly(library, started)
            # The response can stay referenced after the await: a tokio thread
            # releases its reference as a decref that pyo3 defers until its next
            # call into Python. `gc` sees neither pending decrefs nor Rust
            # owners, so compare reference counts with an object only a list
            # holds.
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
            # No name holds the response or the pending `execute()` call, so
            # `pair`'s evaluated arguments hold the only reference to it.
            pair(
                responses.pop() if case == "response" else transport.execute(request),
                boom(),
            )
        except ValueError:
            if case == "response":
                await wait_briefly(library, closed)  # the Drop cancelled the body task


if __name__ == "__main__":
    if tuple(sys.argv[1:]) not in CASES:
        sys.exit(f"unknown library and case: {sys.argv[1:]}")
    library, case = sys.argv[1:]
    sys.unraisablehook = record_unraisable
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if library == "trio":
        trio.run(main, library, case, url)
    else:
        asyncio.run(main(library, case, url))
    assert not unraisables, unraisables  # noqa: S101
    print("ok", flush=True)  # noqa: T201

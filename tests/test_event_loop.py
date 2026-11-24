from __future__ import annotations

import asyncio
import contextvars
import gc
import socket
import threading
import time
import weakref

import pytest

from pyqwest import Client, HTTPTransport

try:
    import uvloop

    new_event_loop = uvloop.new_event_loop
except ImportError:
    import winloop  # ty: ignore[import-not-found]

    new_event_loop = winloop.new_event_loop

# These tests drive their own event loops.
pytestmark = pytest.mark.asyncio_only

loop_factories = pytest.mark.parametrize(
    "loop_factory", [asyncio.new_event_loop, new_event_loop], ids=["asyncio", "uvloop"]
)


async def _get_ok(client: Client, url: str) -> None:
    res = await client.get(f"{url}/echo")
    assert res.status == 200


@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True)
@loop_factories
def test_alternating_loops(url: str, loop_factory) -> None:
    transport = HTTPTransport()
    client = Client(transport)

    async def requests() -> None:
        await asyncio.gather(*(_get_ok(client, url) for _ in range(10)))

    async def close() -> None:
        await transport.aclose()

    # Two loops that take turns on one thread each complete their own requests.
    with asyncio.Runner(loop_factory=loop_factory) as first:
        with asyncio.Runner(loop_factory=loop_factory) as second:
            for _ in range(3):
                first.run(requests())
                second.run(requests())
        # A loop that replaces a closed one completes its requests too.
        with asyncio.Runner(loop_factory=loop_factory) as third:
            third.run(requests())
        first.run(close())


class _Marker:
    pass


_var: contextvars.ContextVar[_Marker] = contextvars.ContextVar("_var")


@pytest.mark.parametrize("http_scheme", ["http"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True)
@loop_factories
def test_loop_does_not_retain_request_context(url: str, loop_factory) -> None:
    async def first_request() -> weakref.ref[_Marker]:
        marker = _Marker()
        _var.set(marker)
        async with HTTPTransport() as transport:
            await _get_ok(Client(transport), url)
        return weakref.ref(marker)

    with asyncio.Runner(loop_factory=loop_factory) as runner:
        # The first request on a loop sets it up, which must not keep the
        # request's context alive as long as the loop. The runner keeps its own
        # context, so the request runs in a new one that is dropped afterwards.
        marker = runner.run(first_request(), context=contextvars.Context())
        gc.collect()
        assert marker() is None


@loop_factories
def test_closed_loop_released(loop_factory) -> None:
    accepted = threading.Event()
    release = threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]

        def respond_when_released() -> None:
            conn, _ = server.accept()
            with conn:
                conn.recv(65536)
                accepted.set()
                release.wait()
                conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 2\r\n\r\nok")

        thread = threading.Thread(target=respond_when_released)
        thread.start()
        client = Client(HTTPTransport())
        loop = loop_factory()
        # The request's task is still pending when the loop is dropped, which the
        # loop would report.
        loop.set_exception_handler(lambda _loop, _context: None)
        task = loop.create_task(client.get(f"http://127.0.0.1:{port}/"))
        loop.run_until_complete(loop.run_in_executor(None, accepted.wait))
        loop.close()
        loop_ref = weakref.ref(loop)
        del loop, task

        # The request completes after its loop closed, with nothing to run the
        # completion, which must not keep the loop alive.
        release.set()
        thread.join()
        for _ in range(100):
            gc.collect()
            if loop_ref() is None:
                break
            time.sleep(0.05)
        assert loop_ref() is None

from __future__ import annotations

import contextlib
import gc
import logging
import signal
import socket
import threading
import warnings
from typing import TYPE_CHECKING

import pytest
import trio
import trio.testing

from pyqwest import HTTPTransport, Request
from pyqwest._trio import await_kickoff, spawn_pump

from ._util import RUN_TIMEOUT, one_connection_server, run_trio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from pyqwest._trio import Completed, Completion

# These tests reach paths of the trio bridge that the kitchensink server cannot
# produce on demand. The kickoff tests drive `await_kickoff` with a fake
# kickoff, so each race resolves the same way on every run. The pump tests call
# `spawn_pump` directly. The body tests serve raw sockets, so they build their
# own transport rather than using the parametrized fixtures.


def trio_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "pyqwest._trio"]


class FakeKickoff:
    """Stands in for the Rust `Kickoff`, reacting to `start` and `abort` as told."""

    def __init__(
        self,
        *,
        on_start: Callable[[Completion], None] | None = None,
        on_abort: Callable[[Completion], None] | None = None,
    ) -> None:
        self._on_start = on_start
        self._on_abort = on_abort
        self._completion: Completion | None = None
        self.started = False
        self.aborted = False

    def start(self, completion: Completion) -> FakeKickoff:
        self.started = True
        self._completion = completion
        if self._on_start is not None:
            self._on_start(completion)
        return self

    def abort(self) -> None:
        self.aborted = True
        if self._on_abort is not None and self._completion is not None:
            self._on_abort(self._completion)


def test_result_racing_abort_is_returned() -> None:
    kickoff = FakeKickoff(on_abort=lambda complete: complete("value", None, False))
    results: list[object] = []

    async def main() -> None:
        with trio.move_on_after(0.01):
            results.append(await await_kickoff(kickoff))
            await trio.lowlevel.checkpoint()
            results.append("not cancelled")

    run_trio(main)
    assert kickoff.aborted
    assert results == ["value"]


def test_unrequested_cancellation_raises() -> None:
    kickoff = FakeKickoff(on_start=lambda complete: complete(None, None, True))

    async def main() -> None:
        with pytest.raises(RuntimeError, match="without a trio cancellation"):
            await await_kickoff(kickoff)

    run_trio(main)


def test_failing_done_callback_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    kickoff = FakeKickoff(on_start=lambda complete: complete("value", None, False))

    def on_done(_: Completed) -> None:
        msg = "callback failed"
        raise RuntimeError(msg)

    async def main() -> None:
        assert await await_kickoff(kickoff, on_done) == "value"

    run_trio(main)
    records = trio_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Exception in request done callback"
    assert "RuntimeError: callback failed" in logging.Formatter().format(record)


def test_cancelled_caller_does_not_start_request() -> None:
    # on_abort matters only if the request wrongly starts: that then fails
    # instead of hanging.
    kickoff = FakeKickoff(on_abort=lambda complete: complete(None, None, True))
    done: list[Completed] = []

    async def main() -> None:
        with trio.CancelScope() as scope:
            scope.cancel()
            await await_kickoff(kickoff, done.append)

    run_trio(main)
    assert not kickoff.started
    assert len(done) == 1
    with pytest.raises(trio.Cancelled):
        done[0].result()


def test_done_callback_keeps_error_traceback() -> None:
    kickoff = FakeKickoff(
        on_start=lambda complete: complete(None, ValueError("request failed"), False)
    )

    def on_done(completed: Completed) -> None:
        with contextlib.suppress(ValueError):
            completed.result()

    async def main() -> None:
        await await_kickoff(kickoff, on_done)

    with pytest.raises(ValueError, match="request failed") as info:
        run_trio(main)
    assert "result" not in [entry.name for entry in info.traceback]


def test_keyboard_interrupt_while_starting_aborts_request() -> None:
    def interrupt(_: Completion) -> None:
        # raise_signal runs the handler before it returns, so the interrupt
        # arrives between start() and parking.
        signal.raise_signal(signal.SIGINT)

    kickoff = FakeKickoff(
        on_start=interrupt, on_abort=lambda complete: complete(None, None, True)
    )

    async def main() -> None:
        # A missed interrupt then fails with TooSlowError instead of hanging.
        with trio.fail_after(5):
            await await_kickoff(kickoff)

    # trio handles SIGINT only when Python's default handler is installed.
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        # trio.run, not run_trio: a signal handler runs on the main thread only.
        with pytest.raises(KeyboardInterrupt):
            trio.run(main)
    finally:
        signal.signal(signal.SIGINT, previous)
    assert kickoff.aborted


def test_body_task_error_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def body_task() -> None:
        msg = "body failed"
        raise ValueError(msg)

    async def main() -> None:
        spawn_pump(body_task)
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    records = trio_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Exception in request body task"
    assert "ValueError: body failed" in logging.Formatter().format(record)


def test_body_task_error_beside_cancellation_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing_child() -> None:
        try:
            await trio.sleep_forever()
        finally:
            msg = "body failed"
            raise ValueError(msg)

    async def body_task() -> None:
        # Cancelling this nursery raises a group of Cancelled and ValueError.
        async with trio.open_nursery() as nursery:
            nursery.start_soon(failing_child)
            await trio.sleep_forever()

    async def main() -> None:
        handle = spawn_pump(body_task)
        await trio.testing.wait_all_tasks_blocked()
        handle.cancel()
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    records = trio_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Exception in request body task"
    assert "ValueError: body failed" in logging.Formatter().format(record)


def test_request_body_backpressure() -> None:
    chunk = b"x" * 65536
    n = 1024  # 64 MiB, far more than the socket buffers hold
    produced = 0
    started = trio.Event()
    release = threading.Event()

    # Reads nothing until released, then drains the chunked body and answers.
    def handle(conn: socket.socket) -> None:
        release.wait(timeout=RUN_TIMEOUT)
        tail = b""
        while not tail.endswith(b"0\r\n\r\n"):
            data = conn.recv(1 << 20)
            if not data:
                return
            tail = (tail + data)[-5:]
        conn.sendall(b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n")

    async def content() -> AsyncIterator[bytes]:
        nonlocal produced
        for _ in range(n):
            produced += 1
            started.set()
            yield chunk

    async def main(url: str) -> None:
        statuses: list[int] = []
        async with HTTPTransport() as transport, trio.open_nursery() as nursery:

            async def execute() -> None:
                res = await transport.execute(Request("POST", url, content=content()))
                statuses.append(res.status)

            nursery.start_soon(execute)
            await started.wait()
            # Once the socket buffers and the body channel are full, the pump
            # waits on a send and stops asking the generator for chunks.
            last = -1
            while produced != last:
                last = produced
                await trio.sleep(0.2)
            assert produced < n
            release.set()
        assert statuses == [200]
        assert produced == n

    with one_connection_server(handle) as url:
        try:
            run_trio(main, url)
        finally:
            release.set()


def test_request_body_closed_on_early_response() -> None:
    def handle(conn: socket.socket) -> None:
        conn.recv(65536)
        # Answer without reading the body, so the client stops sending it.
        conn.sendall(
            b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\nconnection: close\r\n\r\n"
        )
        conn.shutdown(socket.SHUT_WR)
        conn.settimeout(RUN_TIMEOUT)
        with contextlib.suppress(OSError):
            while conn.recv(1 << 20):
                pass

    closed = trio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            while True:
                await trio.sleep(0.01)
                yield b"x" * 1000
        finally:
            closed.set()

    async def main(url: str) -> None:
        with trio.fail_after(RUN_TIMEOUT):
            async with HTTPTransport() as transport:
                res = await transport.execute(Request("POST", url, content=content()))
                # The pump finds the body channel closed and closes the body.
                await closed.wait()
                async for _ in res.content:
                    pass
                await res.aclose()

    with (
        one_connection_server(handle) as url,
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        run_trio(main, url)
        gc.collect()
    assert [
        str(w.message) for w in caught if issubclass(w.category, ResourceWarning)
    ] == []

from __future__ import annotations

import contextlib
import contextvars
import gc
import inspect
import logging
import socket
import threading
import warnings
from typing import TYPE_CHECKING

import pytest
import trio
import trio.testing

from pyqwest import HTTPTransport, Request
from pyqwest._trio import spawn_pump, start_request

from ._util import RUN_TIMEOUT, one_connection_server, run_trio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from pyqwest._trio import Completed, Completion

# These tests reach paths of the trio bridge that the kitchensink server cannot
# produce on demand. The request tests call `start_request` in place of Rust
# and then the completion as tokio would, so each race resolves the same way
# on every run. The pump tests call `spawn_pump` directly. The body tests serve
# raw sockets, so they build their own transport rather than using the
# parametrized fixtures.


def trio_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "pyqwest._trio"]


class FakeAbort:
    """Stands in for the Rust `Abort` handle."""

    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


def close_unawaited(awaitable: Awaitable[object]) -> None:
    """Drops an awaitable without awaiting it, as a caller that never awaits does."""
    assert inspect.iscoroutine(awaitable)
    awaitable.close()


def test_done_callback_runs_before_awaiter_resumes() -> None:
    abort = FakeAbort()
    events: list[tuple[str, object]] = []

    def on_done(completed: Completed) -> None:
        events.append(("done", completed.result()))

    async def main() -> None:
        completion, awaitable = start_request(abort, on_done)
        # tokio reports from one of its threads.
        threading.Thread(target=completion, args=("value", None, False)).start()
        events.append(("awaited", await awaitable))

    run_trio(main)
    assert events == [("done", "value"), ("awaited", "value")]
    assert not abort.aborted


def test_error_keeps_its_traceback_past_done_callback() -> None:
    abort = FakeAbort()

    def on_done(completed: Completed) -> None:
        with contextlib.suppress(ValueError):
            completed.result()

    async def main() -> None:
        completion, awaitable = start_request(abort, on_done)
        completion(None, ValueError("request failed"), False)
        await awaitable

    with pytest.raises(ValueError, match="request failed") as info:
        run_trio(main)
    assert "result" not in [entry.name for entry in info.traceback]


def test_done_callback_sees_caller_context() -> None:
    request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id")
    seen: list[str] = []

    async def main() -> None:
        request_id.set("abc")
        completion, awaitable = start_request(
            FakeAbort(), lambda _: seen.append(request_id.get("unset"))
        )
        # tokio reports from one of its threads, and trio runs `deliver` in its
        # own context; the done callback must still see the caller's.
        threading.Thread(target=completion, args=("value", None, False)).start()
        await awaitable

    run_trio(main)
    assert seen == ["abc"]


def test_failing_done_callback_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    abort = FakeAbort()

    def on_done(_: Completed) -> None:
        msg = "callback failed"
        raise RuntimeError(msg)

    async def main() -> None:
        completion, awaitable = start_request(abort, on_done)
        completion("value", None, False)
        assert await awaitable == "value"

    run_trio(main)
    records = trio_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Exception in request done callback"
    assert "RuntimeError: callback failed" in logging.Formatter().format(record)


def test_cancelled_wait_aborts_request() -> None:
    abort = FakeAbort()
    done: list[Completed] = []

    async def main() -> None:
        completion, awaitable = start_request(abort, done.append)
        with trio.move_on_after(0.01) as scope:
            await awaitable
        assert scope.cancelled_caught
        assert abort.aborted
        assert done == []
        # tokio confirms the abort; the done callback sees the cancellation.
        completion(None, None, True)
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    assert len(done) == 1
    with pytest.raises(trio.Cancelled):
        done[0].result()


def test_result_racing_cancellation_reaches_done_callback() -> None:
    # The request finished before the abort landed. The awaiter has already
    # left with `Cancelled`, so the value is dropped, but the done callback
    # still gets it, so a response's body task is still cancelled.
    abort = FakeAbort()
    done: list[Completed] = []

    async def main() -> None:
        completion, awaitable = start_request(abort, done.append)
        with trio.move_on_after(0.01) as scope:
            await awaitable
        assert scope.cancelled_caught
        assert abort.aborted
        completion("value", None, False)
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    assert [completed.result() for completed in done] == ["value"]


def test_unawaited_request_still_reports() -> None:
    # Dropping the awaitable does not cancel the request, as under asyncio; its
    # done callback still runs once tokio reports.
    abort = FakeAbort()
    done: list[Completed] = []

    async def main() -> None:
        completion, awaitable = start_request(abort, done.append)
        close_unawaited(awaitable)
        completion("value", None, False)
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    assert not abort.aborted
    assert [completed.result() for completed in done] == ["value"]


def test_completion_after_run_finished_is_dropped() -> None:
    # tokio may report after `trio.run` returned. Nothing waits by then, and
    # the Rust side ends the operation instead.
    abort = FakeAbort()
    done: list[Completed] = []
    completions: list[Completion] = []

    async def main() -> None:
        completion, awaitable = start_request(abort, done.append)
        close_unawaited(awaitable)
        completions.append(completion)

    run_trio(main)
    completions[0]("value", None, False)
    assert done == []


class BodyAbort(BaseException):
    pass


# Anything escaping the pump's system task would end the whole trio run.
BODY_ERRORS = [ValueError, SystemExit, KeyboardInterrupt, BodyAbort]


@pytest.mark.parametrize("error", BODY_ERRORS)
def test_body_task_error_is_logged(
    caplog: pytest.LogCaptureFixture, error: type[BaseException]
) -> None:
    async def body_task() -> None:
        msg = "body failed"
        raise error(msg)

    async def main() -> None:
        spawn_pump(body_task)
        await trio.testing.wait_all_tasks_blocked()

    run_trio(main)
    records = trio_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "Exception in request body task"
    assert f"{error.__name__}: body failed" in logging.Formatter().format(record)


@pytest.mark.parametrize("error", BODY_ERRORS)
def test_body_task_error_beside_cancellation_is_logged(
    caplog: pytest.LogCaptureFixture, error: type[BaseException]
) -> None:
    async def failing_child() -> None:
        try:
            await trio.sleep_forever()
        finally:
            msg = "body failed"
            raise error(msg)

    async def body_task() -> None:
        # Cancelling this nursery raises a group of Cancelled and the child's error.
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
    assert f"{error.__name__}: body failed" in logging.Formatter().format(record)


async def sleep_in_nursery() -> None:
    # Cancelling this nursery raises a group holding only Cancelled.
    async with trio.open_nursery() as nursery:
        nursery.start_soon(trio.sleep_forever)
        await trio.sleep_forever()


@pytest.mark.parametrize("sleep", [trio.sleep_forever, sleep_in_nursery])
@pytest.mark.parametrize("cancel", [True, False], ids=["cancelled", "run-ended"])
def test_stopped_body_task_is_not_logged(
    caplog: pytest.LogCaptureFixture, sleep: Callable[[], Awaitable[None]], cancel: bool
) -> None:
    # Without `cancel`, the pump is still running when the run ends, and the
    # run's shutdown cancels it.
    stopped = False

    async def body_task() -> None:
        nonlocal stopped
        try:
            await sleep()
        finally:
            stopped = True

    async def main() -> None:
        handle = spawn_pump(body_task)
        await trio.testing.wait_all_tasks_blocked()
        if cancel:
            handle.cancel()
            await trio.testing.wait_all_tasks_blocked()
            assert stopped

    run_trio(main)
    assert stopped
    assert trio_records(caplog) == []


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

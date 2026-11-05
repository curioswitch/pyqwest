"""Trio side of the runtime bridge.

Rust runs a request on pyqwest's tokio runtime and, before spawning it, asks
`start_request` for the completion callback tokio reports the outcome through
and for the awaitable handed back to Python. The awaitable waits on a
`trio.Event` that the completion sets through the trio token, and aborts the
tokio task if trio cancels the wait first. As under asyncio, the request runs
whether or not the awaitable is awaited.

This module is imported only once trio is the running library, so trio stays
an optional dependency.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import math
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

import trio

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine
    from typing import Any, TypeAlias

    # (value, error, cancelled) — one of the three describes the outcome.
    Completion: TypeAlias = Callable[[object, BaseException | None, bool], None]

_logger = logging.getLogger(__name__)

T = TypeVar("T")


class AbortHandle(Protocol):
    """Rust's handle on a spawned request: `abort()` cancels it."""

    def abort(self) -> None: ...


class PumpHandle(Protocol):
    """A detached task: `cancel()` on its run's thread, `cancel_soon()` from any."""

    def cancel(self) -> None: ...

    def cancel_soon(self) -> None: ...


class Completed:
    """Stands in for the `asyncio.Future` a done callback reads `result()` from."""

    __slots__ = ("_error", "_value")

    def __init__(self, value: object, error: BaseException | None) -> None:
        self._value = value
        self._error = error

    def result(self) -> object:
        if self._error is not None:
            raise self._error
        return self._value


def start_request(
    abort: AbortHandle, on_done: Callable[[Completed], object] | None = None
) -> tuple[Completion, Awaitable[object]]:
    """Prepares for a request tokio is about to run.

    Returns the completion tokio calls with `(value, error, cancelled)` from one
    of its threads, and the awaitable for the outcome. `on_done` runs on the
    trio thread once tokio reports, before the awaiter resumes and whether or
    not anything awaits, as an asyncio done callback does.
    """
    token = trio.lowlevel.current_trio_token()
    # Need to manually propagate context vars into deliver for a system task.
    context = contextvars.copy_context()
    done = trio.Event()
    outcome: list[tuple[object, BaseException | None]] = []
    # The exception that cancelled the wait, which then aborted the request.
    cancelled_by: list[BaseException] = []

    def completion(
        value: object,
        error: BaseException | None,
        cancelled: bool,  # noqa: FBT001  # positional call from Rust
    ) -> None:
        # Runs on a tokio thread; only the token may touch trio state.
        def deliver() -> None:
            if cancelled:
                # Only an aborted wait cancels the request, so the done callback
                # sees the exception the awaiter did.
                error_: BaseException | None = (
                    cancelled_by[0]
                    if cancelled_by
                    else RuntimeError("request cancelled without a trio cancellation")
                )
            else:
                error_ = error
            value_ = value if error_ is None else None
            outcome.append((value_, error_))
            try:
                if on_done is not None:
                    tb = error_.__traceback__ if error_ is not None else None
                    context.run(_call_on_done, on_done, Completed(value_, error_))
                    if error_ is not None:
                        # on_done reads the error by raising it, which adds frames.
                        error_.__traceback__ = tb
            finally:
                done.set()

        # The trio run already finished; nobody is waiting.
        with contextlib.suppress(trio.RunFinishedError):
            token.run_sync_soon(deliver)

    async def wait() -> object:
        try:
            await done.wait()
        except BaseException as e:
            cancelled_by.append(e)
            abort.abort()
            raise
        value, error = outcome[0]
        if error is not None:
            raise error
        return value

    return completion, wait()


def _call_on_done(on_done: Callable[[Completed], object], completed: Completed) -> None:
    # The awaiter's outcome stands; a failing callback is logged, as asyncio
    # logs a failing done callback.
    try:
        on_done(completed)
    except Exception:
        _logger.exception("Exception in request done callback")


class _ScopeHandle:
    """The `PumpHandle` of a trio system task, cancelled through its scope."""

    __slots__ = ("_scope", "_token")

    def __init__(self, token: trio.lowlevel.TrioToken, scope: trio.CancelScope) -> None:
        self._token = token
        self._scope = scope

    def cancel(self) -> None:
        self._scope.cancel()

    def cancel_soon(self) -> None:
        with contextlib.suppress(trio.RunFinishedError):
            self._token.run_sync_soon(self._scope.cancel)


def spawn_pump(
    fn: Callable[..., Awaitable[None]],
    *args: object,
    name: str = "pyqwest request body",
) -> PumpHandle:
    """Start `fn(*args)` as a system task with the caller's context.

    The request body can stream after `execute()` returns (full-duplex
    HTTP/2), so the pump cannot live in the caller's scope. An exception
    escaping a system task ends the whole run, so everything but a
    cancellation, which trio absorbs, is logged here.
    """
    scope = trio.CancelScope()

    async def run() -> None:
        with scope:
            try:
                await fn(*args)
            except (trio.Cancelled, GeneratorExit):
                raise
            except BaseExceptionGroup as group:
                cancelled, errors = group.split(trio.Cancelled)
                if errors is not None:
                    _logger.exception("Exception in request body task", exc_info=errors)
                if cancelled is not None:
                    raise cancelled from None
            except BaseException:
                _logger.exception("Exception in request body task")

    trio.lowlevel.spawn_system_task(run, name=name, context=contextvars.copy_context())
    return _ScopeHandle(trio.lowlevel.current_trio_token(), scope)


class _Task:
    """The `pyqwest._runtime.Task` of a system task from `TrioRuntime.spawn`."""

    __slots__ = ("_done", "_handle")

    def __init__(self, handle: PumpHandle, done: trio.Event) -> None:
        self._handle = handle
        self._done = done

    def cancel(self) -> None:
        self._handle.cancel()

    async def wait(self) -> None:
        await self._done.wait()


class _Queue(Generic[T]):
    """The `pyqwest._runtime.Queue` of a trio memory channel."""

    __slots__ = ("_receive", "_send")

    def __init__(self, max_size: int | None) -> None:
        self._send, self._receive = trio.open_memory_channel[T](
            math.inf if max_size is None else max_size
        )

    async def put(self, item: T) -> None:
        await self._send.send(item)

    def put_nowait(self, item: T) -> None:
        self._send.send_nowait(item)

    async def get(self) -> T:
        return await self._receive.receive()


class TrioRuntime:
    """`pyqwest._runtime.Runtime` under trio."""

    __slots__ = ("_token",)

    def __init__(self) -> None:
        self._token = trio.lowlevel.current_trio_token()

    async def sleep(self, seconds: float) -> None:
        await trio.sleep(seconds)

    async def checkpoint(self) -> None:
        await trio.lowlevel.checkpoint()

    def new_event(self) -> trio.Event:
        return trio.Event()

    def new_queue(self, max_size: int | None = None) -> _Queue[T]:
        return _Queue(max_size)

    def spawn(self, fn: Callable[[], Coroutine[Any, Any, None]], name: str) -> _Task:
        done = trio.Event()

        async def run() -> None:
            # trio runs a system task even when its scope is cancelled before it
            # starts, so the finally block always sets the event.
            try:
                await fn()
            finally:
                done.set()

        return _Task(spawn_pump(run, name=name), done)

    def call_soon_threadsafe(
        self, callback: Callable[..., None], *args: object
    ) -> None:
        with contextlib.suppress(trio.RunFinishedError):
            self._token.run_sync_soon(callback, *args)

    def shield(self) -> trio.CancelScope:
        # Cancellation is level-triggered: cleanup after it runs only shielded.
        return trio.CancelScope(shield=True)

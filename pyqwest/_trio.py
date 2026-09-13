"""Trio side of the runtime bridge.

Rust hands over a `Kickoff`: a future on pyqwest's tokio runtime that has not
started. `await_kickoff` starts it and parks the trio task on
`trio.lowlevel.wait_task_rescheduled` until tokio reports the outcome through
the trio token, and aborts the tokio task if trio cancels first.

This module is imported only once trio is the running library, so trio stays
an optional dependency.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import sys
from typing import TYPE_CHECKING, Protocol

import outcome
import trio

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup  # ty: ignore[unresolved-import]

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import TypeAlias

    from trio.lowlevel import RaiseCancelT

    from ._glue import PumpHandle

    # (value, error, cancelled) — one of the three describes the outcome.
    Completion: TypeAlias = Callable[[object, BaseException | None, bool], None]

_logger = logging.getLogger(__name__)


class AbortHandle(Protocol):
    def abort(self) -> None: ...


class Kickoff(Protocol):
    def start(self, completion: Completion) -> AbortHandle: ...


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


# KeyboardInterrupt must not land between starting the future and parking,
# or the future's completion would later reschedule a task that is not
# waiting.
@trio.lowlevel.enable_ki_protection
async def await_kickoff(
    kickoff: Kickoff, on_done: Callable[[Completed], object] | None = None
) -> object:
    task = trio.lowlevel.current_task()
    token = trio.lowlevel.current_trio_token()
    raise_cancel: RaiseCancelT | None = None

    def completion(
        value: object,
        error: BaseException | None,
        cancelled: bool,  # noqa: FBT001  # positional call from Rust
    ) -> None:
        # Runs on a tokio thread; only the token may touch trio state.
        def deliver() -> None:
            if cancelled:
                if raise_cancel is None:
                    # trio did not ask for it: the abort handle was dropped.
                    err = RuntimeError(
                        "request task stopped without a trio cancellation"
                    )
                    result: outcome.Outcome[object] = outcome.Error(err)
                else:
                    result = outcome.capture(raise_cancel)
            elif error is not None:
                result = outcome.Error(error)
            else:
                result = outcome.Value(value)
            trio.lowlevel.reschedule(task, result)

        # The trio run already finished; nobody is waiting.
        with contextlib.suppress(trio.RunFinishedError):
            token.run_sync_soon(deliver)

    def abort_fn(raise_cancel_: RaiseCancelT) -> trio.lowlevel.Abort:
        nonlocal raise_cancel
        raise_cancel = raise_cancel_
        abort.abort()
        # tokio reports the abort (or a completion that raced it) through
        # `completion`, which reschedules us.
        return trio.lowlevel.Abort.FAILED

    try:
        # An already-cancelled caller never starts the request.
        await trio.lowlevel.checkpoint_if_cancelled()
        abort = kickoff.start(completion)
        value = await trio.lowlevel.wait_task_rescheduled(abort_fn)
    except BaseException as e:
        if on_done is not None:
            tb = e.__traceback__
            _call_on_done(on_done, Completed(None, e))
            # on_done reads the error by raising it, which adds its frames.
            e.__traceback__ = tb
        raise
    if on_done is not None:
        _call_on_done(on_done, Completed(value, None))
    return value


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


# Protected so KeyboardInterrupt cannot land between spawning the task and
# returning its handle, which would leave the task running with no way to
# cancel it.
@trio.lowlevel.enable_ki_protection
def spawn_pump(fn: Callable[..., Awaitable[None]], *args: object) -> PumpHandle:
    """Start `fn(*args)` as a system task with the caller's context.

    The request body can stream after `execute()` returns (full-duplex
    HTTP/2), so the pump cannot live in the caller's scope. An exception
    escaping a system task ends the whole run, so an `Exception` is logged
    here; anything else, such as a cancellation, propagates.
    """
    scope = trio.CancelScope()

    async def run() -> None:
        with scope:
            try:
                await fn(*args)
            except Exception:
                _logger.exception("Exception in request body task")
            except BaseExceptionGroup as group:
                errors, rest = group.split(Exception)
                if errors is not None:
                    _logger.exception("Exception in request body task", exc_info=errors)
                if rest is not None:
                    raise rest from None

    trio.lowlevel.spawn_system_task(
        run, name="pyqwest request body", context=contextvars.copy_context()
    )
    return _ScopeHandle(trio.lowlevel.current_trio_token(), scope)

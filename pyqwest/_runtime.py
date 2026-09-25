"""The parts of the running async library that pyqwest's Python code needs.

Rust picks the library a request runs on with sniffio. Python code that runs
tasks of its own, such as the retry middleware, picks it the same way here,
without depending on anyio.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Protocol

try:
    import sniffio
except ModuleNotFoundError:
    # trio depends on sniffio, so without it only asyncio can be running.
    sniffio = None  # ty: ignore[invalid-assignment]

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from contextlib import AbstractContextManager
    from typing import Any


class Event(Protocol):
    """A one-shot flag: `asyncio.Event` or `trio.Event`."""

    def set(self) -> None: ...

    async def wait(self) -> object: ...


class Task(Protocol):
    """A detached task: `cancel()` on its run's thread."""

    def cancel(self) -> object: ...


class Runtime(Protocol):
    async def sleep(self, seconds: float) -> None: ...

    async def checkpoint(self) -> None:
        """Raises the current task's cancellation, if any."""
        ...

    def new_event(self) -> Event: ...

    def spawn(self, fn: Callable[[], Coroutine[Any, Any, None]], name: str) -> Task:
        """Start `fn()` as a task that outlives the caller's scope.

        An `Exception` escaping it is logged, not raised anywhere.
        """
        ...

    def call_soon_threadsafe(
        self, callback: Callable[..., None], *args: object
    ) -> None:
        """Run `callback(*args)` on the run's thread, from any thread.

        Does nothing once the run has finished.
        """
        ...

    def shield(self) -> AbstractContextManager[object]:
        """Keeps cleanup that awaits from being cancelled."""
        ...


class AsyncioRuntime:
    """`Runtime` under asyncio."""

    __slots__ = ("_loop",)

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    async def checkpoint(self) -> None:
        await asyncio.sleep(0)

    def new_event(self) -> Event:
        return asyncio.Event()

    def spawn(self, fn: Callable[[], Coroutine[Any, Any, None]], name: str) -> Task:
        task = self._loop.create_task(fn(), name=name)
        task.add_done_callback(_consume_task)
        return task

    def call_soon_threadsafe(
        self, callback: Callable[..., None], *args: object
    ) -> None:
        # Calling into a closed loop raises, and by then nothing can run there.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(callback, *args)

    def shield(self) -> AbstractContextManager[object]:
        # A cancellation is delivered once, so cleanup after it is not cancelled
        # again.
        return contextlib.nullcontext()


def _consume_task(task: asyncio.Task[None]) -> None:
    # Retrieves the outcome so the loop never logs it, as with the request body
    # pump. Cancellation is how the task stops.
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        task.get_loop().call_exception_handler(
            {"message": f"Exception in {task.get_name()} task", "exception": error}
        )


def current_runtime() -> Runtime:
    """The `Runtime` of the library running the current task."""
    library = "asyncio"
    if sniffio is not None:
        with contextlib.suppress(sniffio.AsyncLibraryNotFoundError):
            library = sniffio.current_async_library()
    match library:
        case "asyncio":
            return AsyncioRuntime(asyncio.get_running_loop())
        case "trio":
            # Imported only once trio is the running library, so trio stays an
            # optional dependency.
            from pyqwest._trio import TrioRuntime  # noqa: PLC0415

            return TrioRuntime()
        case _:
            msg = f"pyqwest supports asyncio and trio, not {library}"
            raise RuntimeError(msg)

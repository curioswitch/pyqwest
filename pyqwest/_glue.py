from __future__ import annotations

import asyncio
import contextlib
import inspect
import types
from typing import TYPE_CHECKING, Protocol, TypeVar

from ._multipart import (
    encode_multipart,
    encode_multipart_sync,
    multipart_boundary,
    multipart_content_type,
)
from ._pyqwest import FullResponse, Headers, Request, Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Iterator

    from ._multipart import Multipart, SyncMultipart

T_contra = TypeVar("T_contra", contravariant=True)
U = TypeVar("U")


async def wrap_body_gen(
    gen: AsyncIterator[T_contra],
    wrap_fn: Callable[[T_contra], U],
    start: Awaitable[bool],
) -> AsyncIterator[U]:
    try:
        if not await start:
            return
        async for item in gen:
            yield wrap_fn(item)
    finally:
        try:
            aclose = gen.aclose  # ty: ignore[unresolved-attribute]
        except AttributeError:
            pass
        else:
            await aclose()


async def new_full_response(
    status: int,
    headers: Headers,
    content: AsyncIterator[memoryview | bytes | bytearray],
    trailers: Headers,
) -> FullResponse:
    buf = bytearray()
    try:
        async for chunk in content:
            buf.extend(chunk)
    finally:
        try:
            aclose = content.aclose  # ty: ignore[unresolved-attribute]
        except AttributeError:
            pass
        else:
            await aclose()
    return FullResponse(status, headers, bytes(buf), trailers)


async def execute_and_read_full(transport: Transport, request: Request) -> FullResponse:
    resp = await transport.execute(request)
    return await new_full_response(
        resp.status, resp.headers, resp.content, resp.trailers
    )


def read_content_sync(content: Iterator[bytes | memoryview]) -> bytes:
    buf = bytearray()
    try:
        for chunk in content:
            buf.extend(chunk)
    finally:
        try:
            close = content.close  # ty: ignore[unresolved-attribute]
        except AttributeError:
            pass
        else:
            close()
    return bytes(buf)


def multipart_content(multipart: Multipart) -> tuple[str, AsyncIterator[bytes]]:
    boundary = multipart_boundary()
    return multipart_content_type(boundary), encode_multipart(multipart, boundary)


def multipart_content_sync(multipart: SyncMultipart) -> tuple[str, Iterator[bytes]]:
    boundary = multipart_boundary()
    return multipart_content_type(boundary), encode_multipart_sync(multipart, boundary)


def close_request_iterator(itr: Iterator[bytes]) -> None:
    # Running generators cannot be closed reliably.
    # On Python 3.12, it can cause a hang.
    if (
        isinstance(itr, types.GeneratorType)
        and inspect.getgeneratorstate(itr) == inspect.GEN_RUNNING
    ):
        return

    try:
        close = itr.close  # ty: ignore[unresolved-attribute]
    except AttributeError:
        pass
    else:
        with contextlib.suppress(Exception):
            close()


class PumpHandle(Protocol):
    """A detached task: `cancel()` on its loop's thread, `cancel_soon()` from any."""

    def cancel(self) -> None: ...

    def cancel_soon(self) -> None: ...


class _TaskHandle:
    """The `PumpHandle` of an asyncio task."""

    __slots__ = ("_task",)

    def __init__(self, task: asyncio.Task[None]) -> None:
        self._task = task

    def cancel(self) -> None:
        self._task.cancel()

    def cancel_soon(self) -> None:
        # A closed loop raises RuntimeError; its task is already gone.
        with contextlib.suppress(RuntimeError):
            self._task.get_loop().call_soon_threadsafe(self._task.cancel)


def _consume_task(task: asyncio.Task[None]) -> None:
    # Retrieve the outcome so the loop never logs it: the pump reports
    # errors through its sender, and cancellation is how it is stopped.
    with contextlib.suppress(asyncio.CancelledError):
        task.exception()


def spawn_pump(
    fn: Callable[..., Coroutine[object, object, None]], *args: object
) -> PumpHandle:
    """Start `fn(*args)` as a detached asyncio task."""
    task = asyncio.get_running_loop().create_task(fn(*args))
    task.add_done_callback(_consume_task)
    return _TaskHandle(task)


# Vendored from pyo3-async-runtimes to apply some fixes


class Sender(Protocol[T_contra]):
    def send(self, item: T_contra | BaseException) -> bool | Awaitable[bool]: ...

    def close(self) -> None: ...


async def forward(gen: AsyncIterator[T_contra], sender: Sender[T_contra]) -> None:
    try:
        async for item in gen:
            should_continue = sender.send(item)

            if inspect.isawaitable(should_continue):
                should_continue = await should_continue

            if should_continue:
                continue
            break
    except Exception as e:
        res = sender.send(e)
        if inspect.isawaitable(res):
            await res
    finally:
        sender.close()
        # Close the body here, not from garbage collection, which is late and,
        # under trio, warns.
        aclose = getattr(gen, "aclose", None)
        if aclose is not None:
            await aclose()

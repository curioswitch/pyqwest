from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Protocol, TypeVar

from ._pyqwest import FullResponse, Headers, Request, Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

T_contra = TypeVar("T_contra", contravariant=True)
U = TypeVar("U")


async def wrap_body_gen(
    gen: AsyncIterator[T_contra], wrap_fn: Callable[[T_contra], U]
) -> AsyncIterator[U]:
    try:
        async for item in gen:
            yield wrap_fn(item)
    finally:
        try:
            aclose = gen.aclose  # type: ignore[attr-defined]
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
            aclose = content.aclose  # type: ignore[attr-defined]
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
            close = content.close  # type: ignore[attr-defined]
        except AttributeError:
            pass
        else:
            close()
    return bytes(buf)


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

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from .pyqwest import FullResponse, Headers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

T = TypeVar("T")
U = TypeVar("U")


async def wrap_body_gen(
    gen: AsyncIterator[T], wrap_fn: Callable[[T], U]
) -> AsyncIterator[U]:
    i = 0
    try:
        async for item in gen:
            i += len(item)
            yield wrap_fn(item)
    finally:
        print(f"Total bytes consumed by generator: {i}")
        try:
            aclose = gen.aclose  # type: ignore[attr-defined]
        except AttributeError:
            pass
        else:
            await aclose()


async def new_full_response(
    status: int, headers: Headers, content: AsyncIterator[bytes], trailers: Headers
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

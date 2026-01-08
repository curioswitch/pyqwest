from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from .pyqwest import FullResponse, Headers, Request, Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

T = TypeVar("T")
U = TypeVar("U")


async def wrap_body_gen(
    gen: AsyncIterator[T], wrap_fn: Callable[[T], U]
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


async def execute_and_read_full(transport: Transport, request: Request) -> FullResponse:
    resp = await transport.execute(request)
    return await new_full_response(
        resp.status, resp.headers, resp.content, resp.trailers
    )

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

T = TypeVar("T")
U = TypeVar("U")


async def wrap_body_gen(
    gen: AsyncIterator[T], wrap_fn: Callable[[T], U]
) -> AsyncIterator[U]:
    async for item in gen:
        yield wrap_fn(item)

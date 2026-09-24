from __future__ import annotations

import anyio.lowlevel
import pytest

from pyqwest import Client, Request, Response
from pyqwest import Transport as BaseTransport
from pyqwest.middleware.retry import RetryTransport
from pyqwest.middleware.retry._async import RetryingRequestContent

# test_async.py runs on asyncio only, because RetryTransport retries only under
# asyncio. It serves a request that needs no retry on any backend.


@pytest.mark.anyio
async def test_streamed_content_without_retry() -> None:
    class Transport(BaseTransport):
        def __init__(self) -> None:
            self.read_content = b""

        async def execute(self, request: Request) -> Response:
            assert not isinstance(request.content, bytes)
            async for chunk in request.content:
                self.read_content += chunk
            return Response(status=200, content=b"")

    async def content():
        yield b"Hello "
        await anyio.lowlevel.checkpoint()
        yield b"world!"

    transport = Transport()
    res = await Client(RetryTransport(transport)).put(
        "http://localhost", content=content()
    )
    assert res.status == 200
    assert transport.read_content == b"Hello world!"


@pytest.mark.anyio
async def test_retrying_content_error() -> None:
    async def content():
        yield b"Hello "
        msg = "boom"
        raise ValueError(msg)

    retrying = RetryingRequestContent(content())
    with pytest.raises(ValueError, match="boom"):
        async for _ in retrying.get():
            pass
    assert not retrying.retryable
    with pytest.raises(RuntimeError, match="cannot be retried"):
        await anext(retrying.get())

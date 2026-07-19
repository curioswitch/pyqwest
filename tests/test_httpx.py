from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING

import httpx
import pytest

from pyqwest.httpx import AsyncPyqwestTransport, PyqwestTransport
from pyqwest.testing import ASGITransport, WSGITransport

if TYPE_CHECKING:
    import sys
    from collections.abc import AsyncIterator, Iterable

    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

    if sys.version_info >= (3, 11):
        from wsgiref.types import StartResponse, WSGIEnvironment
    else:
        from _typeshed.wsgi import StartResponse, WSGIEnvironment


async def echo_app(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    if scope["type"] != "http":
        return
    content = b""
    while True:
        message = await receive()
        if message["type"] == "http.request":
            content += message.get("body", b"")
            if not message.get("more_body", False):
                break
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"x-request-method", scope["method"].encode("utf-8"))],
            "trailers": False,
        }
    )
    await send({"type": "http.response.body", "body": content, "more_body": False})


def sync_echo_app(
    environ: WSGIEnvironment, start_response: StartResponse
) -> Iterable[bytes]:
    content = environ["wsgi.input"].read()
    start_response("200 OK", [("x-request-method", environ["REQUEST_METHOD"])])
    return [content]


@pytest.mark.asyncio
async def test_async_get() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.get("http://localhost/")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "GET"
    assert res.content == b""


@pytest.mark.asyncio
async def test_async_post_content() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "POST"
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_post_content_iterator() -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"Hello "
        yield b"world!"

    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.post("http://localhost/", content=content())
    assert res.status_code == 200
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_response_stream() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with (
        httpx.AsyncClient(transport=transport) as client,
        client.stream("POST", "http://localhost/", content=b"Hello world!") as res,
    ):
        assert res.status_code == 200
        content = b""
        async for chunk in res.aiter_raw():
            content += chunk
    assert content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_no_timeout() -> None:
    transport = AsyncPyqwestTransport(ASGITransport(echo_app))
    async with httpx.AsyncClient(transport=transport, timeout=None) as client:  # noqa: S113
        res = await client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.content == b"Hello world!"


@pytest.mark.asyncio
async def test_async_timeout_headers() -> None:
    release = asyncio.Event()

    async def app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await release.wait()
        await echo_app(scope, receive, send)

    transport = AsyncPyqwestTransport(ASGITransport(app))
    try:
        async with httpx.AsyncClient(transport=transport, timeout=0.1) as client:
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                await client.get("http://localhost/")
    finally:
        release.set()
        # Let the application task finish before the event loop closes.
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_async_timeout_response_content() -> None:
    release = asyncio.Event()

    async def app(
        scope: Scope, _receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "http":
            return
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
                "trailers": False,
            }
        )
        await send(
            {"type": "http.response.body", "body": b"partial", "more_body": True}
        )
        await release.wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    transport = AsyncPyqwestTransport(ASGITransport(app))
    try:
        async with (
            httpx.AsyncClient(transport=transport, timeout=0.2) as client,
            client.stream("GET", "http://localhost/") as res,
        ):
            assert res.status_code == 200
            content = b""
            with pytest.raises((TimeoutError, asyncio.TimeoutError)):
                async for chunk in res.aiter_raw():
                    content += chunk
            assert content == b"partial"
    finally:
        release.set()
        await asyncio.sleep(0.01)


def test_sync_get() -> None:
    transport = PyqwestTransport(WSGITransport(sync_echo_app))
    with httpx.Client(transport=transport) as client:
        res = client.get("http://localhost/")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "GET"
    assert res.content == b""


def test_sync_post_content() -> None:
    transport = PyqwestTransport(WSGITransport(sync_echo_app))
    with httpx.Client(transport=transport) as client:
        res = client.post("http://localhost/", content=b"Hello world!")
    assert res.status_code == 200
    assert res.headers["x-request-method"] == "POST"
    assert res.content == b"Hello world!"


def test_sync_timeout() -> None:
    release = threading.Event()

    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        release.wait(5)
        return sync_echo_app(environ, start_response)

    transport = PyqwestTransport(WSGITransport(app))
    try:
        with (
            httpx.Client(transport=transport, timeout=0.1) as client,
            pytest.raises(TimeoutError),
        ):
            client.get("http://localhost/")
    finally:
        release.set()

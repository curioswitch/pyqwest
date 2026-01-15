from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyqwest import FullResponse, Headers, HTTPVersion, Response, SyncResponse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.mark.asyncio
async def test_response_minimal():
    response = Response(status=404)
    assert response.status == 404
    assert response.http_version == HTTPVersion.HTTP1
    assert response.headers == Headers()
    assert await anext(response.content, None) is None
    assert response.trailers == Headers()
    assert not response._read_pending  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_response_content_bytes():
    response = Response(
        status=500,
        http_version=HTTPVersion.HTTP2,
        headers=Headers({"content-type": "text/plain"}),
        content=b"Sample body",
        trailers=Headers({"x-trailer": "info"}),
    )

    assert response.status == 500
    assert response.http_version == HTTPVersion.HTTP2
    assert response.headers == {"content-type": "text/plain"}
    content = response.content
    assert await anext(content) == b"Sample body"
    assert await anext(content, None) is None


@pytest.mark.asyncio
async def test_response_content_iterator():
    async def content() -> AsyncIterator[bytes]:
        yield b"Part 1, "
        yield b"Part 2."

    response = Response(status=200, content=content())

    assert response.status == 200
    assert response.http_version == HTTPVersion.HTTP1
    assert response.headers == {}
    parts = []
    async for chunk in response.content:
        parts.append(chunk)
    assert parts == [b"Part 1, ", b"Part 2."]


def test_sync_response_minimal():
    response = SyncResponse(status=404)
    assert response.status == 404
    assert response.http_version == HTTPVersion.HTTP1
    assert response.headers == Headers()
    assert next(response.content, None) is None
    assert response.trailers == Headers()
    assert not response._read_pending  # pyright: ignore[reportAttributeAccessIssue]


def test_sync_response_content_bytes():
    response = SyncResponse(
        status=500,
        http_version=HTTPVersion.HTTP2,
        headers=Headers({"content-type": "text/plain"}),
        content=b"Sample body",
        trailers=Headers({"x-trailer": "info"}),
    )

    assert response.status == 500
    assert response.http_version == HTTPVersion.HTTP2
    assert response.headers == {"content-type": "text/plain"}
    content = response.content
    assert next(content) == b"Sample body"
    assert next(content, None) is None


def test_sync_response_content_iterator():
    def content() -> Iterator[bytes]:
        yield b"Part 1, "
        yield b"Part 2."

    response = SyncResponse(status=200, content=content())
    assert response.status == 200
    assert response.http_version == HTTPVersion.HTTP1
    assert response.headers == {}
    parts = []
    for chunk in response.content:
        parts.append(chunk)
    assert parts == [b"Part 1, ", b"Part 2."]


def test_full_response_decode_utf8_no_content_type():
    response = FullResponse(200, Headers(), "日本語".encode(), Headers())
    assert response.text() == "日本語"


def test_full_response_decode_utf8_content_type_no_charset():
    response = FullResponse(
        200, Headers({"content-type": "text/plain"}), "日本語".encode(), Headers()
    )
    assert response.text() == "日本語"


def test_full_response_decode_utf8_content_charset():
    response = FullResponse(
        200,
        Headers({"content-type": "text/plain; charset=shift_jis"}),
        "日本語".encode("shift_jis"),
        Headers(),
    )
    assert response.text() == "日本語"

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import Headers, Request, SyncRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.mark.asyncio
async def test_request_minimal():
    request = Request(method="GET", url="https://example.com/")
    assert request.method == "GET"
    assert request.url == "https://example.com/"
    assert request.headers == Headers()
    chunks = []
    async for chunk in request.content:
        chunks.append(chunk)
    assert chunks == []


def test_sync_request_minimal():
    request = SyncRequest(method="GET", url="https://example.com/")
    assert request.method == "GET"
    assert request.url == "https://example.com/"
    assert request.headers == Headers()
    chunks = list(request.content)
    assert chunks == []


@pytest.mark.asyncio
async def test_request_content_bytes():
    request = Request(
        method="DELETE",
        url="https://example.com/resource?id=123",
        headers=Headers({"authorization": "Bearer token"}),
        content=b"Sample body",
    )

    assert request.method == "DELETE"
    assert request.url == "https://example.com/resource?id=123"
    assert request.headers["authorization"] == "Bearer token"
    chunks = []
    async for chunk in request.content:
        chunks.append(chunk)
    assert chunks == [b"Sample body"]


def test_sync_request_content_bytes():
    request = SyncRequest(
        method="DELETE",
        url="https://example.com/resource?id=123",
        headers=Headers({"authorization": "Bearer token"}),
        content=b"Sample body",
    )

    assert request.method == "DELETE"
    assert request.url == "https://example.com/resource?id=123"
    assert request.headers["authorization"] == "Bearer token"
    chunks = list(request.content)
    assert chunks == [b"Sample body"]


@pytest.mark.asyncio
async def test_request_content_iterator():
    async def content() -> AsyncIterator[bytes]:
        yield b"Part 1, "
        yield b"Part 2."

    request = Request(
        method="DELETE", url="https://example.com/resource?id=123", content=content()
    )

    assert request.method == "DELETE"
    assert request.url == "https://example.com/resource?id=123"
    assert request.headers == {}
    parts = []
    async for chunk in request.content:
        parts.append(chunk)
    assert parts == [b"Part 1, ", b"Part 2."]


def test_sync_request_content_iterator():
    def content() -> Iterator[bytes]:
        yield b"Part 1, "
        yield b"Part 2."

    request = SyncRequest(
        method="DELETE", url="https://example.com/resource?id=123", content=content()
    )

    assert request.method == "DELETE"
    assert request.url == "https://example.com/resource?id=123"
    assert request.headers == {}
    parts = list(request.content)
    assert parts == [b"Part 1, ", b"Part 2."]


@pytest.mark.asyncio
async def test_request_content_invalid():
    with pytest.raises(TypeError) as excinfo:
        Request(
            method="DELETE",
            url="https://example.com/resource?id=123",
            content=cast("bytes", "invalid"),
        )

    assert str(excinfo.value) == "Content must be bytes or an async iterator of bytes"


def test_sync_request_content_invalid():
    with pytest.raises(TypeError) as excinfo:
        SyncRequest(
            method="DELETE",
            url="https://example.com/resource?id=123",
            content=cast("bytes", 10),
        )

    assert str(excinfo.value) == "'int' object is not iterable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE", "CUSTOM"],
)
async def test_request_methods(method: str):
    request = Request(method=method, url="https://example.com/")
    assert request.method == method


@pytest.mark.parametrize(
    "method",
    ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT", "TRACE", "CUSTOM"],
)
def test_sync_request_methods(method: str):
    request = SyncRequest(method=method, url="https://example.com/")
    assert request.method == method

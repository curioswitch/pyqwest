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


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    ("params", "expected"),
    [
        pytest.param(
            {"key1": "value1", "key2": "value2"},
            "https://example.com/?existing=bar&key1=value1&key2=value2",
            id="simple dict",
        ),
        pytest.param(
            {"key1": "value with spaces", "key2": "value/with/special?chars&"},
            "https://example.com/?existing=bar&key1=value+with+spaces&key2=value%2Fwith%2Fspecial%3Fchars%26",
            id="dict with special characters",
        ),
        pytest.param(
            {"key1": "value1", "key2": None},
            "https://example.com/?existing=bar&key1=value1&key2",
            id="dict with None value",
        ),
        pytest.param(
            [("key1", "value1"), ("key2", "value2")],
            "https://example.com/?existing=bar&key1=value1&key2=value2",
            id="simple list of tuples",
        ),
        pytest.param(
            [("key1", "value with spaces"), ("key2", "value/with/special?chars&")],
            "https://example.com/?existing=bar&key1=value+with+spaces&key2=value%2Fwith%2Fspecial%3Fchars%26",
            id="list of tuples with special characters",
        ),
        pytest.param(
            [("key1", "value1"), ("key2", None)],
            "https://example.com/?existing=bar&key1=value1&key2",
            id="list of tuples with None value",
        ),
        pytest.param(
            [("key1", "value1"), ("key1", "value2")],
            "https://example.com/?existing=bar&key1=value1&key1=value2",
            id="list of tuples with multiple values for same key",
        ),
    ],
)
def test_request_query_params(
    mode: str,
    params: dict[str, str | None] | list[tuple[str, str | None]],
    expected: str,
):
    if mode == "sync":
        request = SyncRequest(
            method="GET", url="https://example.com/?existing=bar", params=params
        )
    else:
        request = Request(
            method="GET", url="https://example.com/?existing=bar", params=params
        )

    assert request.url == expected


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.asyncio
async def test_request_json_content(mode: str):
    if mode == "sync":
        request = SyncRequest(
            method="POST", url="https://example.com/api", content={"key": "value"}
        )
        content = b"".join(request.content)
    else:
        request = Request(
            method="POST", url="https://example.com/api", content={"key": "value"}
        )
        chunks = []
        async for chunk in request.content:
            chunks.append(chunk)
        content = b"".join(chunks)

    assert request.method == "POST"
    assert request.url == "https://example.com/api"
    # Request represents input headers, JSON is appended during transport. So here
    # we don't have it content type.
    assert request.headers == {}
    assert content == b'{"key": "value"}'

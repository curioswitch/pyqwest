from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest

from pyqwest import Headers, Multipart, Request, SyncMultipart, SyncRequest


@pytest.mark.asyncio
async def test_request_minimal():
    request = Request(method="GET", url="https://example.com/")
    assert request.method == "GET"
    assert request.url == "https://example.com/"
    assert request.headers == Headers()
    assert isinstance(request.content, bytes)
    assert request.content == b""


def test_sync_request_minimal():
    request = SyncRequest(method="GET", url="https://example.com/")
    assert request.method == "GET"
    assert request.url == "https://example.com/"
    assert request.headers == Headers()
    assert isinstance(request.content, bytes)
    assert request.content == b""


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
    assert isinstance(request.content, bytes)
    assert request.content == b"Sample body"


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
    assert isinstance(request.content, bytes)
    assert request.content == b"Sample body"


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
    assert isinstance(request.content, AsyncIterator)
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

    assert (
        str(excinfo.value)
        == "Content must be bytes, an async iterator of bytes, or Multipart"
    )


def test_sync_request_content_invalid():
    with pytest.raises(TypeError) as excinfo:
        SyncRequest(
            method="DELETE",
            url="https://example.com/resource?id=123",
            content=cast("bytes", 10),
        )

    assert str(excinfo.value) == "'int' object is not iterable"


def multipart_boundary_from_headers(headers: Headers) -> str:
    content_type = headers["content-type"]
    prefix = "multipart/form-data; boundary="
    assert content_type.startswith(prefix)
    return content_type.removeprefix(prefix)


def expected_multipart_body(boundary: str) -> bytes:
    return (
        f"--{boundary}\r\n"
        'content-disposition: form-data; name="field"\r\n'
        "\r\n"
        "value\r\n"
        f"--{boundary}--\r\n"
    ).encode()


@pytest.mark.asyncio
async def test_request_content_multipart():
    headers = Headers({"content-type": "multipart/form-data", "x-hello": "world"})
    request = Request(
        method="POST",
        url="https://example.com/upload",
        headers=headers,
        content=Multipart({"field": b"value"}),
    )

    boundary = multipart_boundary_from_headers(request.headers)
    assert request.headers["x-hello"] == "world"
    # The request headers are a copy, leaving the provided headers unchanged.
    assert headers["content-type"] == "multipart/form-data"

    assert isinstance(request.content, AsyncIterator)
    content = bytearray()
    async for chunk in cast("AsyncIterator[bytes]", request.content):
        content.extend(chunk)
    assert bytes(content) == expected_multipart_body(boundary)


def test_sync_request_content_multipart():
    headers = Headers({"content-type": "multipart/form-data", "x-hello": "world"})
    request = SyncRequest(
        method="POST",
        url="https://example.com/upload",
        headers=headers,
        content=SyncMultipart({"field": b"value"}),
    )

    boundary = multipart_boundary_from_headers(request.headers)
    assert request.headers["x-hello"] == "world"
    # The request headers are a copy, leaving the provided headers unchanged.
    assert headers["content-type"] == "multipart/form-data"

    assert isinstance(request.content, Iterator)
    content = cast("Iterator[bytes]", request.content)
    assert b"".join(content) == expected_multipart_body(boundary)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_request_multipart_other_content_type(mode: str):
    headers = Headers({"content-type": "text/plain"})
    with pytest.raises(ValueError, match="must be unset or multipart/form-data"):
        if mode == "sync":
            SyncRequest(
                method="POST",
                url="https://example.com/upload",
                headers=headers,
                content=SyncMultipart({"field": b"value"}),
            )
        else:
            Request(
                method="POST",
                url="https://example.com/upload",
                headers=headers,
                content=Multipart({"field": b"value"}),
            )


@pytest.mark.asyncio
async def test_request_multipart_unique_boundaries():
    multipart = Multipart({"field": b"value"})
    boundaries = {
        multipart_boundary_from_headers(
            Request(
                method="POST", url="https://example.com/upload", content=multipart
            ).headers
        )
        for _ in range(2)
    }
    assert len(boundaries) == 2


@pytest.mark.asyncio
async def test_request_content_sync_multipart():
    multipart = SyncMultipart({"field": b"value"})
    with pytest.raises(TypeError) as excinfo:
        Request(
            method="POST",
            url="https://example.com/upload",
            content=cast("Multipart", multipart),
        )
    assert (
        str(excinfo.value)
        == "Content must be bytes, an async iterator of bytes, or Multipart"
    )


def test_sync_request_content_async_multipart():
    multipart = Multipart({"field": b"value"})
    with pytest.raises(TypeError) as excinfo:
        SyncRequest(
            method="POST",
            url="https://example.com/upload",
            content=cast("SyncMultipart", multipart),
        )
    assert str(excinfo.value) == "'Multipart' object is not iterable"


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
        assert isinstance(request.content, bytes)
        content = request.content
    else:
        request = Request(
            method="POST", url="https://example.com/api", content={"key": "value"}
        )
        assert isinstance(request.content, bytes)
        content = request.content

    assert request.method == "POST"
    assert request.url == "https://example.com/api"
    # Request represents input headers, JSON is appended during transport. So here
    # we don't have it content type.
    assert request.headers == {}
    assert content == b'{"key": "value"}'

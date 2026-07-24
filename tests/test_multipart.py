from __future__ import annotations

import asyncio
import email.parser
from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import Client, Multipart, Part, SyncClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from email.message import Message

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2", "h3", "auto"], indirect=True),
]


def parse_multipart(
    content_type: str, body: bytes
) -> list[tuple[str | None, str | None, str | None, bytes]]:
    message = email.parser.BytesParser().parsebytes(
        f"content-type: {content_type}\r\n\r\n".encode() + body
    )
    assert message.is_multipart()
    parts = []
    for part in cast("list[Message]", message.get_payload()):
        parts.append(
            (
                cast(
                    "str | None", part.get_param("name", header="content-disposition")
                ),
                part.get_filename(),
                part.get_content_type() if "content-type" in part else None,
                cast("bytes", part.get_payload(decode=True)),
            )
        )
    return parts


@pytest.mark.asyncio
async def test_multipart(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):

        def stream_sync() -> Iterator[bytes]:
            yield b"stream "
            yield b"chunks"

        multipart = Multipart(
            [
                ("field", "hello world"),
                ("data", b"\x00\x01binary"),
                ("file", Part(b"file content", filename="hello.txt")),
                (
                    "stream",
                    Part(
                        stream_sync(),
                        filename="stream.bin",
                        content_type="application/octet-stream",
                    ),
                ),
            ]
        )
        resp = await asyncio.to_thread(client.post, url, content=multipart)
    else:

        async def stream_async() -> AsyncIterator[bytes]:
            yield b"stream "
            yield b"chunks"

        multipart = Multipart(
            [
                ("field", "hello world"),
                ("data", b"\x00\x01binary"),
                ("file", Part(b"file content", filename="hello.txt")),
                (
                    "stream",
                    Part(
                        stream_async(),
                        filename="stream.bin",
                        content_type="application/octet-stream",
                    ),
                ),
            ]
        )
        resp = await client.post(url, content=multipart)

    assert resp.status == 200
    content_type = resp.headers["x-echo-content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert parse_multipart(content_type, resp.content) == [
        ("field", None, None, b"hello world"),
        ("data", None, None, b"\x00\x01binary"),
        ("file", "hello.txt", None, b"file content"),
        ("stream", "stream.bin", "application/octet-stream", b"stream chunks"),
    ]


@pytest.mark.asyncio
async def test_multipart_overrides_content_type(
    client: Client | SyncClient, url: str
) -> None:
    url = f"{url}/echo"
    headers = [("content-type", "text/plain")]
    multipart = Multipart({"field": b"value"})
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.post, url, headers, multipart)
    else:
        resp = await client.post(url, headers, multipart)

    assert resp.status == 200
    content_type = resp.headers["x-echo-content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert parse_multipart(content_type, resp.content) == [
        ("field", None, None, b"value")
    ]


@pytest.mark.asyncio
async def test_multipart_multiple_streams(
    client: Client | SyncClient, url: str
) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):

        def stream_sync(chunks: list[bytes]) -> Iterator[bytes]:
            yield from chunks

        multipart = Multipart(
            [
                ("first", Part(stream_sync([b"first ", b"stream"]))),
                ("second", Part(stream_sync([b"second ", b"stream"]))),
            ]
        )
        resp = await asyncio.to_thread(client.post, url, content=multipart)
    else:

        async def stream_async(chunks: list[bytes]) -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        multipart = Multipart(
            [
                ("first", Part(stream_async([b"first ", b"stream"]))),
                ("second", Part(stream_async([b"second ", b"stream"]))),
            ]
        )
        resp = await client.post(url, content=multipart)

    assert resp.status == 200
    content_type = resp.headers["x-echo-content-type"]
    assert parse_multipart(content_type, resp.content) == [
        ("first", None, None, b"first stream"),
        ("second", None, None, b"second stream"),
    ]


@pytest.mark.asyncio
async def test_multipart_stream_error(client: Client | SyncClient, url: str) -> None:
    # There is a race between whether the error is handled on the request
    # or response side, which can look like a connection error when the server
    # aborts or a response error. We match any.
    with pytest.raises(Exception, match=r"Request|connection|reading content"):
        method = "POST"
        url = f"{url}/echo"
        if isinstance(client, SyncClient):

            def stream_sync() -> Iterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            def run():
                multipart = Multipart({"file": Part(stream_sync())})
                with client.stream(method, url, content=multipart) as resp:
                    b"".join(resp.content)

            await asyncio.to_thread(run)
        else:

            async def stream_async() -> AsyncIterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            multipart = Multipart({"file": Part(stream_async())})
            async with client.stream(method, url, content=multipart) as resp:
                content = b""
                async for chunk in resp.content:
                    content += chunk

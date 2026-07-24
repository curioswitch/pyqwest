from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import Multipart, Part, WriteError
from pyqwest._multipart import encode_multipart_async, encode_multipart_sync

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def test_escapes_part_name_and_filename() -> None:
    # Matches reqwest's percent-encoding, notably keeping CR/LF and quotes
    # out of the part headers.
    multipart = Multipart([('a"b\r\nc', Part(b"x", filename='f"\r\n /%.txt'))])
    body = b"".join(encode_multipart_sync(multipart, "boundary"))
    assert b'name="a%22b%0D%0Ac"' in body
    assert b'filename="f%22%0D%0A%20%2F%25.txt"' in body


def test_encode_sync_rejects_async_iterator() -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b"chunk"

    multipart = Multipart({"file": Part(stream())})
    with pytest.raises(TypeError) as excinfo:
        encode_multipart_sync(multipart, "boundary")
    assert (
        str(excinfo.value) == "Part content must be bytes, str, or an iterator of bytes"
    )


@pytest.mark.asyncio
async def test_encode_async_rejects_sync_iterator() -> None:
    multipart = Multipart({"file": Part(iter([b"chunk"]))})
    with pytest.raises(TypeError) as excinfo:
        encode_multipart_async(multipart, "boundary")
    assert (
        str(excinfo.value)
        == "Part content must be bytes, str, or an async iterator of bytes"
    )


def test_encode_sync_rejects_non_bytes_chunk() -> None:
    multipart = Multipart({"file": Part(cast("Iterator[bytes]", iter(["text"])))})
    with pytest.raises(WriteError, match="Request not bytes object"):
        b"".join(encode_multipart_sync(multipart, "boundary"))


@pytest.mark.asyncio
async def test_encode_async_rejects_non_bytes_chunk() -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield cast("bytes", "text")

    multipart = Multipart({"file": Part(stream())})
    content = encode_multipart_async(multipart, "boundary")
    with pytest.raises(WriteError, match="Request not bytes object"):
        async for _ in content:
            pass


@pytest.mark.asyncio
async def test_encode_async_closes_part_stream_on_close() -> None:
    closed = False

    async def stream() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"first"
            yield b"second"
        finally:
            closed = True

    multipart = Multipart({"file": Part(stream())})
    content = encode_multipart_async(multipart, "boundary")
    async for chunk in content:
        if chunk == b"first":
            break
    await content.aclose()  # ty: ignore[unresolved-attribute]
    assert closed


def test_encode_sync_closes_part_stream_on_close() -> None:
    closed = False

    def stream() -> Iterator[bytes]:
        nonlocal closed
        try:
            yield b"first"
            yield b"second"
        finally:
            closed = True

    multipart = Multipart({"file": Part(stream())})
    content = encode_multipart_sync(multipart, "boundary")
    for chunk in content:
        if chunk == b"first":
            break
    content.close()  # ty: ignore[unresolved-attribute]
    assert closed

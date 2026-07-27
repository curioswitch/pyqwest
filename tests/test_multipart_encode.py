from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyqwest import Multipart, Part, SyncMultipart, SyncPart
from pyqwest._multipart import encode_multipart, encode_multipart_sync

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


def test_escapes_part_name_and_filename() -> None:
    # Matches reqwest's percent-encoding, notably keeping CR/LF and quotes
    # out of the part headers.
    multipart = SyncMultipart([('a"b\r\nc', SyncPart(b"x", filename='f"\r\n /%.txt'))])
    body = b"".join(encode_multipart_sync(multipart, "boundary"))
    assert b'name="a%22b%0D%0Ac"' in body
    assert b'filename="f%22%0D%0A%20%2F%25.txt"' in body


def test_encodes_part_headers() -> None:
    part = SyncPart(
        b"x", headers={"content-type": "text/plain", "x-part-meta": "hello"}
    )
    body = b"".join(encode_multipart_sync(SyncMultipart({"field": part}), "boundary"))
    assert b"content-type: text/plain\r\n" in body
    assert b"x-part-meta: hello\r\n" in body


@pytest.mark.parametrize("part_class", [Part, SyncPart])
def test_part_invalid_header_value(part_class: type[Part | SyncPart]) -> None:
    with pytest.raises(ValueError, match="Invalid header value"):
        part_class(b"x", headers={"content-type": "text/pl\r\nain"})


def test_multipart_accepts_mapping() -> None:
    class PartsMapping:
        def items(self) -> list[tuple[str, bytes]]:
            return [("field", b"value")]

    multipart = SyncMultipart(PartsMapping())  # ty: ignore[invalid-argument-type]
    assert [(name, part.content) for name, part in multipart.parts] == [
        ("field", b"value")
    ]


@pytest.mark.asyncio
async def test_encode_closes_part_stream_on_close() -> None:
    closed = False

    async def stream() -> AsyncIterator[bytes]:
        nonlocal closed
        try:
            yield b"first"
            yield b"second"
        finally:
            closed = True

    multipart = Multipart({"file": Part(stream())})
    content = encode_multipart(multipart, "boundary")
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

    multipart = SyncMultipart({"file": SyncPart(stream())})
    content = encode_multipart_sync(multipart, "boundary")
    for chunk in content:
        if chunk == b"first":
            break
    content.close()  # ty: ignore[unresolved-attribute]
    assert closed

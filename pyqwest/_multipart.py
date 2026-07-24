from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from ._pyqwest import WriteError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator

    from ._pyqwest import Multipart, Part


def multipart_boundary() -> str:
    return secrets.token_hex(16)


def multipart_content_type(boundary: str) -> str:
    return f"multipart/form-data; boundary={boundary}"


# The escaped characters match reqwest's percent-encoding of part names and
# filenames (the WHATWG path-segment set), so that requests sent through the
# testing transports put the same bytes on the wire as real requests. Notably,
# this keeps CR/LF out of the part headers.
_ESCAPE_CHARS = frozenset(' "<>`#?{}/%' + "".join(map(chr, range(0x20))) + "\x7f")


def _escape(value: str) -> str:
    return "".join(f"%{ord(c):02X}" if c in _ESCAPE_CHARS else c for c in value)


def _part_header(boundary: str, part_name: str, part: Part) -> bytes:
    lines = [f"--{boundary}"]
    disposition = f'content-disposition: form-data; name="{_escape(part_name)}"'
    if part.filename is not None:
        disposition += f'; filename="{_escape(part.filename)}"'
    lines.append(disposition)
    if part.content_type is not None:
        lines.append(f"content-type: {part.content_type}")
    lines.extend(["", ""])
    return "\r\n".join(lines).encode()


def encode_multipart_sync(multipart: Multipart, boundary: str) -> Iterator[bytes]:
    parts = multipart.parts
    # Validate upfront, matching when the real transport raises.
    for _, part in parts:
        content = part.content
        if not isinstance(content, bytes) and not hasattr(content, "__iter__"):
            msg = "Part content must be bytes, str, or an iterator of bytes"
            raise TypeError(msg)
    return _encode_sync(parts, boundary)


def _encode_sync(parts: list[tuple[str, Part]], boundary: str) -> Iterator[bytes]:
    for part_name, part in parts:
        yield _part_header(boundary, part_name, part)
        content = part.content
        if isinstance(content, bytes):
            yield content
        else:
            itr = iter(cast("Iterable[bytes]", content))
            try:
                for chunk in itr:
                    if not isinstance(chunk, bytes):
                        msg = "Request not bytes object"
                        raise WriteError(msg)
                    yield chunk
            finally:
                close = getattr(itr, "close", None)
                if close is not None:
                    close()
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


def encode_multipart_async(multipart: Multipart, boundary: str) -> AsyncIterator[bytes]:
    parts = multipart.parts
    # Validate upfront, matching when the real transport raises.
    for _, part in parts:
        content = part.content
        if not isinstance(content, bytes) and not hasattr(content, "__aiter__"):
            msg = "Part content must be bytes, str, or an async iterator of bytes"
            raise TypeError(msg)
    return _encode_async(parts, boundary)


async def _encode_async(
    parts: list[tuple[str, Part]], boundary: str
) -> AsyncIterator[bytes]:
    for part_name, part in parts:
        yield _part_header(boundary, part_name, part)
        content = part.content
        if isinstance(content, bytes):
            yield content
        else:
            itr = aiter(cast("AsyncIterator[bytes]", content))
            try:
                async for chunk in itr:
                    if not isinstance(chunk, bytes):
                        msg = "Request not bytes object"
                        raise WriteError(msg)
                    yield chunk
            finally:
                # Closing this generator does not cascade to the part's
                # iterator, so close it explicitly to keep cancellation
                # from leaving it to be finalized at an arbitrary point.
                aclose = getattr(itr, "aclose", None)
                if aclose is not None:
                    await aclose()
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()

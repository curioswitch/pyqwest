from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from ._pyqwest import WriteError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator, Mapping


class Part:
    """A single part of a multipart form."""

    __slots__ = ("_content", "_content_type", "_filename")

    def __init__(
        self,
        content: bytes | str | Iterable[bytes] | AsyncIterator[bytes],
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """Creates a new Part object.

        Args:
            content: The content of the part. A str will be encoded as UTF-8.
                     An iterator of bytes will be streamed - use a synchronous
                     iterator with SyncRequest and an asynchronous iterator
                     with Request.
            filename: The filename to send in the part's content-disposition header.
            content_type: The content type of the part.

        Raises:
            TypeError: If the content is not bytes, str, or an iterator.
            ValueError: If the content type is invalid.
        """
        if isinstance(content, str):
            content = content.encode()
        elif not isinstance(content, bytes) and not (
            hasattr(content, "__iter__") or hasattr(content, "__aiter__")
        ):
            msg = "Part content must be bytes, str, or an iterator of bytes"
            raise TypeError(msg)
        if content_type is not None:
            _validate_content_type(content_type)
        self._content = content
        self._filename = filename
        self._content_type = content_type

    @property
    def content(self) -> bytes | Iterable[bytes] | AsyncIterator[bytes]:
        """Returns the content of the part."""
        return self._content

    @property
    def filename(self) -> str | None:
        """Returns the filename of the part."""
        return self._filename

    @property
    def content_type(self) -> str | None:
        """Returns the content type of the part."""
        return self._content_type


class Multipart:
    """Multipart form request content.

    Passing a Multipart object as request content encodes it into the request
    content as a multipart/form-data request. The multipart boundary is
    generated when constructing the request, and the request uses a copy of
    the provided headers with the content-type header set to match the
    boundary, replacing any user-provided content-type.
    """

    __slots__ = ("_parts",)

    def __init__(
        self,
        parts: Mapping[str, Part | bytes | str]
        | Iterable[tuple[str, Part | bytes | str]],
    ) -> None:
        """Creates a new Multipart object.

        Args:
            parts: The named parts of the form. bytes or str values are
                   converted to parts without a filename or content type.

        Raises:
            TypeError: If a part name is not str or a part is not Part, bytes, or str.
        """
        items = (
            cast("Mapping[str, Part | bytes | str]", parts).items()
            if hasattr(parts, "items")
            else cast("Iterable[tuple[str, Part | bytes | str]]", parts)
        )
        converted: list[tuple[str, Part]] = []
        for name, part in items:
            if not isinstance(name, str):
                msg = "Part name must be str"
                raise TypeError(msg)
            converted.append((name, part if isinstance(part, Part) else Part(part)))
        self._parts = converted

    @property
    def parts(self) -> list[tuple[str, Part]]:
        """Returns the named parts of the form."""
        return list(self._parts)


def _validate_content_type(content_type: str) -> None:
    type_, sep, subtype = content_type.split(";", 1)[0].partition("/")
    if (
        not type_.strip()
        or not sep
        or not subtype.strip()
        or any(ord(c) < 0x20 or ord(c) == 0x7F for c in content_type)
    ):
        msg = f"Invalid content type: {content_type}"
        raise ValueError(msg)


def multipart_boundary() -> str:
    return secrets.token_hex(16)


def multipart_content_type(boundary: str) -> str:
    return f"multipart/form-data; boundary={boundary}"


# The escaped characters match reqwest's percent-encoding of part names and
# filenames (the WHATWG path-segment set), so that requests put the same bytes
# on the wire regardless of transport. Notably, this keeps CR/LF and quotes
# out of the part headers.
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
    # Validate upfront so errors raise when constructing the request rather
    # than when streaming its content.
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
                # Closing this generator does not cascade to the part's
                # iterator, so close it explicitly.
                close = getattr(itr, "close", None)
                if close is not None:
                    close()
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


def encode_multipart_async(multipart: Multipart, boundary: str) -> AsyncIterator[bytes]:
    parts = multipart.parts
    # Validate upfront so errors raise when constructing the request rather
    # than when streaming its content.
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

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator, Mapping


class Part:
    """A single part of a multipart form, for use with Multipart."""

    __slots__ = ("_content", "_content_type", "_filename")

    def __init__(
        self,
        content: bytes | str | AsyncIterator[bytes],
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """Creates a new Part object.

        Args:
            content: The content of the part. A str will be encoded as UTF-8.
                     An async iterator of bytes will be streamed.
            filename: The filename to send in the part's content-disposition header.
            content_type: The content type of the part.

        Raises:
            ValueError: If the content type is invalid.
        """
        if content_type is not None:
            _validate_content_type(content_type)
        self._content = content.encode() if isinstance(content, str) else content
        self._filename = filename
        self._content_type = content_type

    @property
    def content(self) -> bytes | AsyncIterator[bytes]:
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
    """Multipart form request content for asynchronous requests. For
    synchronous requests, use SyncMultipart.

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
        """
        items = (
            cast("Mapping[str, Part | bytes | str]", parts).items()
            if hasattr(parts, "items")
            else cast("Iterable[tuple[str, Part | bytes | str]]", parts)
        )
        self._parts = [
            (name, part if isinstance(part, Part) else Part(part))
            for name, part in items
        ]

    @property
    def parts(self) -> list[tuple[str, Part]]:
        """Returns the named parts of the form."""
        return list(self._parts)


class SyncPart:
    """A single part of a multipart form, for use with SyncMultipart."""

    __slots__ = ("_content", "_content_type", "_filename")

    def __init__(
        self,
        content: bytes | str | Iterable[bytes],
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> None:
        """Creates a new SyncPart object.

        Args:
            content: The content of the part. A str will be encoded as UTF-8.
                     An iterable of bytes will be streamed.
            filename: The filename to send in the part's content-disposition header.
            content_type: The content type of the part.

        Raises:
            ValueError: If the content type is invalid.
        """
        if content_type is not None:
            _validate_content_type(content_type)
        self._content = content.encode() if isinstance(content, str) else content
        self._filename = filename
        self._content_type = content_type

    @property
    def content(self) -> bytes | Iterable[bytes]:
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


class SyncMultipart:
    """Multipart form request content for synchronous requests. For
    asynchronous requests, use Multipart.

    Passing a SyncMultipart object as request content encodes it into the
    request content as a multipart/form-data request. The multipart boundary
    is generated when constructing the request, and the request uses a copy of
    the provided headers with the content-type header set to match the
    boundary, replacing any user-provided content-type.
    """

    __slots__ = ("_parts",)

    def __init__(
        self,
        parts: Mapping[str, SyncPart | bytes | str]
        | Iterable[tuple[str, SyncPart | bytes | str]],
    ) -> None:
        """Creates a new SyncMultipart object.

        Args:
            parts: The named parts of the form. bytes or str values are
                   converted to parts without a filename or content type.
        """
        items = (
            cast("Mapping[str, SyncPart | bytes | str]", parts).items()
            if hasattr(parts, "items")
            else cast("Iterable[tuple[str, SyncPart | bytes | str]]", parts)
        )
        self._parts = [
            (name, part if isinstance(part, SyncPart) else SyncPart(part))
            for name, part in items
        ]

    @property
    def parts(self) -> list[tuple[str, SyncPart]]:
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


def _part_header(boundary: str, part_name: str, part: Part | SyncPart) -> bytes:
    lines = [f"--{boundary}"]
    disposition = f'content-disposition: form-data; name="{_escape(part_name)}"'
    if part.filename is not None:
        disposition += f'; filename="{_escape(part.filename)}"'
    lines.append(disposition)
    if part.content_type is not None:
        lines.append(f"content-type: {part.content_type}")
    lines.extend(["", ""])
    return "\r\n".join(lines).encode()


def encode_multipart_sync(multipart: SyncMultipart, boundary: str) -> Iterator[bytes]:
    for part_name, part in multipart.parts:
        yield _part_header(boundary, part_name, part)
        content = part.content
        if isinstance(content, bytes):
            yield content
        else:
            itr = iter(content)
            try:
                yield from itr
            finally:
                # Closing this generator does not cascade to the part's
                # iterator, so close it explicitly.
                close = getattr(itr, "close", None)
                if close is not None:
                    close()
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()


async def encode_multipart(multipart: Multipart, boundary: str) -> AsyncIterator[bytes]:
    for part_name, part in multipart.parts:
        yield _part_header(boundary, part_name, part)
        content = part.content
        if isinstance(content, bytes):
            yield content
        else:
            itr = aiter(content)
            try:
                async for chunk in itr:
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

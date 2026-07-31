from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast, final

from ._pyqwest import Headers

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Iterator, Mapping

    from ._pyqwest import HTTPHeaderName

    _PartHeaders = (
        Headers
        | Mapping[str | HTTPHeaderName, str]
        | Iterable[tuple[str | HTTPHeaderName, str]]
    )


@final
class Part:
    """A single part of a multipart form, for use with Multipart."""

    __slots__ = ("_content", "_filename", "_headers")

    def __init__(
        self,
        content: bytes | str | AsyncIterator[bytes],
        *,
        filename: str | None = None,
        headers: _PartHeaders | None = None,
    ) -> None:
        """Creates a new Part object.

        Args:
            content: The content of the part. A str will be encoded as UTF-8.
                     An async iterator of bytes will be streamed.
            filename: The filename to send in the part's content-disposition header.
            headers: Additional headers to send with the part, for example
                     content-type.

        Raises:
            ValueError: If a header name or value is invalid.
        """
        self._content = content.encode() if isinstance(content, str) else content
        self._filename = filename
        self._headers = headers if isinstance(headers, Headers) else Headers(headers)

    @property
    def content(self) -> bytes | AsyncIterator[bytes]:
        """Returns the content of the part."""
        return self._content

    @property
    def filename(self) -> str | None:
        """Returns the filename of the part."""
        return self._filename

    @property
    def headers(self) -> Headers:
        """Returns the headers of the part."""
        return self._headers


@final
class Multipart:
    """Multipart form request content for asynchronous requests. For
    synchronous requests, use SyncMultipart.

    Passing a Multipart object as request content encodes it into the request
    content as a multipart/form-data request. The multipart boundary is
    generated when constructing the request, and the request uses a copy of
    the provided headers with the content-type header set to match the
    boundary. The provided headers must not have a content-type other than
    multipart/form-data.
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
                   converted to parts without a filename or headers.
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


@final
class SyncPart:
    """A single part of a multipart form, for use with SyncMultipart."""

    __slots__ = ("_content", "_filename", "_headers")

    def __init__(
        self,
        content: bytes | str | Iterable[bytes],
        *,
        filename: str | None = None,
        headers: _PartHeaders | None = None,
    ) -> None:
        """Creates a new SyncPart object.

        Args:
            content: The content of the part. A str will be encoded as UTF-8.
                     An iterable of bytes will be streamed.
            filename: The filename to send in the part's content-disposition header.
            headers: Additional headers to send with the part, for example
                     content-type.

        Raises:
            ValueError: If a header name or value is invalid.
        """
        self._content = content.encode() if isinstance(content, str) else content
        self._filename = filename
        self._headers = headers if isinstance(headers, Headers) else Headers(headers)

    @property
    def content(self) -> bytes | Iterable[bytes]:
        """Returns the content of the part."""
        return self._content

    @property
    def filename(self) -> str | None:
        """Returns the filename of the part."""
        return self._filename

    @property
    def headers(self) -> Headers:
        """Returns the headers of the part."""
        return self._headers


@final
class SyncMultipart:
    """Multipart form request content for synchronous requests. For
    asynchronous requests, use Multipart.

    Passing a SyncMultipart object as request content encodes it into the
    request content as a multipart/form-data request. The multipart boundary
    is generated when constructing the request, and the request uses a copy of
    the provided headers with the content-type header set to match the
    boundary. The provided headers must not have a content-type other than
    multipart/form-data.
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
                   converted to parts without a filename or headers.
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
    if not any(c in _ESCAPE_CHARS for c in value):
        return value
    return "".join(f"%{ord(c):02X}" if c in _ESCAPE_CHARS else c for c in value)


def _part_header(boundary: str, part_name: str, part: Part | SyncPart) -> bytes:
    lines = [f"--{boundary}"]
    disposition = f'content-disposition: form-data; name="{_escape(part_name)}"'
    if part.filename is not None:
        disposition += f'; filename="{_escape(part.filename)}"'
    lines.append(disposition)
    lines.extend(f"{name}: {value}" for name, value in part.headers.items())
    lines.extend(["", ""])
    return "\r\n".join(lines).encode()


def encode_multipart_sync(multipart: SyncMultipart, boundary: str) -> Iterator[bytes]:
    for part_name, part in multipart.parts:
        yield _part_header(boundary, part_name, part)
        content = part.content
        if isinstance(content, bytes):
            yield content
        else:
            try:
                yield from content
            finally:
                close = getattr(content, "close", None)
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
            try:
                async for chunk in content:
                    yield chunk
            finally:
                aclose = getattr(content, "aclose", None)
                if aclose is not None:
                    await aclose()
        yield b"\r\n"
    yield f"--{boundary}--\r\n".encode()

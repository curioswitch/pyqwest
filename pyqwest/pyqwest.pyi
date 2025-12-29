from collections.abc import (
    AsyncIterator,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    Sequence,
    ValuesView,
)
from typing import TypeVar, overload

_T = TypeVar("_T")

class Headers:
    """Container of HTTP headers.

    This class behaves like a dictionary with case-insensitive keys and
    string values. Standard dictionary access will act as if keys can only
    have a single value. The add method can be used to It additionally can be used to store
    multiple values for the same key by using the [`add`][] method. Iterating over
    values or items will return all values, including duplicates.
    """

    def __init__(
        self, items: Mapping[str, str] | Iterable[tuple[str, str]] | None = None
    ) -> None:
        """Creates a new [`Headers`][] object.

        Args:
            items: Initial headers to add.
        """

    def __getitem__(self, key: str) -> str:
        """Return the header value for the key.

        If multiple values are present for the key, returns the first value.

        Args:
            key: The header name.

        Raises:
            KeyError: If the key is not present.
        """

    def __setitem__(self, key: str, value: str) -> None:
        """Sets the header value for the key, replacing any existing values.

        Args:
            key: The header name.
            value: The header value.
        """

    def __delitem__(self, key: str) -> None:
        """Deletes all values for the key.

        Args:
            key: The header name.

        Raises:
            KeyError: If the key is not present.
        """

    def __iter__(self) -> Iterator[str]:
        """Returns an iterator over the header names."""

    def __len__(self) -> int:
        """Returns the number of unique header names."""

    def __eq__(
        self, other: Headers | Mapping[str, str] | Iterable[tuple[str, str]]
    ) -> bool:
        """Compares the headers for equality with another Headers object,
        mapping, or iterable of key-value pairs.

        Args:
            other: The object to compare against.
        """

    def get(self, key: str, default: str | None = None) -> str | None:
        """Returns the header value for the key, or default if not present.

        Args:
            key: The header name.
            default: The default value to return if the key is not present.
        """

    @overload
    def pop(self, key: str) -> str:
        """Removes and returns the header value for the key.

        Args:
            key: The header name.

        Raises:
            KeyError: If the key is not present.
        """

    @overload
    def pop(self, key: str, default: _T) -> str | _T:
        """Removes and returns the header value for the key, or default if not present.

        Args:
            key: The header name.
            default: The default value to return if the key is not present.
        """

    def popitem(self) -> tuple[str, str]:
        """Removes and returns an arbitrary (name, value) pair. Will return the same
        name multiple times if it has multiple values.

        Raises:
            KeyError: If the headers are empty.
        """

    def setdefault(self, key: str, default: str | None = None) -> str:
        """If the key is not present, sets it to the default value.
        Returns the value for the key.

        Args:
            key: The header name.
            default: The default value to set and return if the key is not present.
        """

    def add(self, key: str, value: str) -> None:
        """Adds a header value for the key. Existing values are preserved.

        Args:
            key: The header name.
            value: The header value.
        """

    @overload
    def update(self, **kwargs: str) -> None:
        """Updates headers from keyword arguments. Existing values are replaced.

        Args:
            **kwargs: Header names and values to set.
        """
    @overload
    def update(
        self, items: Mapping[str, str] | Iterable[tuple[str, str]], /, **kwargs: str
    ) -> None:
        """Updates headers with the provided items. Existing values are replaced.

        Args:
            items: Header names and values to set.
            **kwargs: Additional header names and values to set after items. May overwrite items.
        """

    def clear(self) -> None:
        """Removes all headers."""

    def getall(self, key: str) -> Sequence[str]:
        """Returns all header values for the key.

        Args:
            key: The header name.
        """

    def items(self) -> ItemsView[str, str]:
        """Returns a new view of all header name-value pairs, including duplicates."""

    def keys(self) -> KeysView[str]:
        """Returns a new view of all unique header names."""

    def values(self) -> ValuesView[str]:
        """Returns a new view of all header values, including duplicates."""

    def __contains__(self, key: object) -> bool:
        """Returns True if the header name is present.

        Args:
            key: The header name.
        """

class HTTPVersion:
    HTTP1: HTTPVersion
    HTTP2: HTTPVersion
    HTTP3: HTTPVersion

class Client:
    def __init__(
        self, tls_ca_cert: bytes | None = None, http_version: HTTPVersion | None = None
    ) -> None: ...
    async def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Response: ...

class Request:
    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | Iterable[bytes] | None = None,
    ) -> None: ...

class Response:
    status: int
    http_version: HTTPVersion
    headers: Headers
    content: AsyncIterator[bytes]
    trailers: Headers | None

class SyncClient:
    def __init__(
        self, tls_ca_cert: bytes | None = None, http_version: HTTPVersion | None = None
    ) -> None: ...
    def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
    ) -> SyncResponse: ...

class SyncRequest:
    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
    ) -> None: ...

class SyncResponse:
    status: int
    http_version: HTTPVersion
    headers: Headers
    content: Iterator[bytes]
    trailers: Headers | None

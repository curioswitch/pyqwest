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
from types import TracebackType
from typing import Protocol, TypeVar, overload

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
    def __init__(self, transport: Transport | None = None) -> None: ...
    async def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        timeout: float | None = None,
    ) -> Response: ...

class Transport(Protocol):
    async def execute(self, request: Request) -> Response: ...

class HTTPTransport:
    def __init__(
        self,
        *,
        tls_ca_cert: bytes | None = None,
        http_version: HTTPVersion | None = None,
    ) -> None: ...
    async def __aenter__(self) -> HTTPTransport:
        """Enters the context manager for the transport to automatically close it when
        leaving.
        """

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exits the context manager for the transport, closing it."""

    async def execute(self, request: Request) -> Response: ...
    async def close(self) -> None:
        """Closes the transport, releasing any underlying resources."""

class Request:
    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Creates a new Request object.

        Args:
            method: The HTTP method.
            url: The request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.
        """

    @property
    def method(self) -> str:
        """Returns the HTTP method of the request."""

    @property
    def url(self) -> str:
        """Returns the request URL."""

    @property
    def headers(self) -> Headers:
        """Returns the request headers."""

    @property
    def content(self) -> AsyncIterator[bytes]:
        """Returns an async iterator over the request content."""

    @property
    def timeout(self) -> float | None:
        """Returns the timeout for the request in seconds, or None if not set."""

class Response:
    def __init__(
        self,
        *,
        status: int,
        http_version: HTTPVersion | None = None,
        headers: Headers | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        trailers: Headers | None = None,
    ) -> None:
        """Creates a new [`Response`][] object.

        Care must be taken if your service uses trailers and you override content.
        Trailers will not be received without fully consuming the original response content.
        Patterns that wrap the original response content should not have any issue but if
        you replace it completely and need trailers, make sure to still read and discard
        the original content.

        Args:
            status: The HTTP status code of the response.
            http_version: The HTTP version of the response.
            headers: The response headers.
            content: The response content.
            trailers: The response trailers.
        """

    async def __aenter__(self) -> Response:
        """Enters the context manager for the response to automatically close it when
        leaving.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exits the context manager for the response, closing it."""

    @property
    def status(self) -> int:
        """Returns the HTTP status code of the response."""

    @property
    def http_version(self) -> HTTPVersion:
        """Returns the HTTP version of the response."""

    @property
    def headers(self) -> Headers:
        """Returns the response headers."""

    @property
    def content(self) -> AsyncIterator[bytes]:
        """Returns an asynchronous iterator over the response content."""

    @property
    def trailers(self) -> Headers:
        """Returns the response trailers.

        Because trailers complete the response, this will only be filled after fully
        consuming the [`content`][] iterator.
        """

    async def close(self) -> None:
        """Closes the response, releasing any underlying resources.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

class SyncClient:
    def __init__(self, transport: SyncTransport | None = None) -> None: ...
    def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> SyncResponse: ...

class SyncTransport(Protocol):
    def execute(self, request: SyncRequest) -> SyncResponse: ...

class SyncHTTPTransport:
    def __init__(
        self,
        *,
        tls_ca_cert: bytes | None = None,
        http_version: HTTPVersion | None = None,
    ) -> None: ...
    def __enter__(self) -> SyncHTTPTransport:
        """Enters the context manager for the transport to automatically
        close it when leaving.
        """

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exits the context manager for the transport, closing it."""

    def execute(self, request: SyncRequest) -> SyncResponse: ...
    def close(self) -> None:
        """Closes the transport, releasing any underlying resources."""

class SyncRequest:
    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> None:
        """Creates a new SyncRequest object.

        Args:
            method: The HTTP method.
            url: The request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.
        """

    @property
    def method(self) -> str:
        """Returns the HTTP method of the request."""

    @property
    def url(self) -> str:
        """Returns the request URL."""

    @property
    def headers(self) -> Headers:
        """Returns the request headers."""

    @property
    def content(self) -> Iterator[bytes]:
        """Returns an iterator over the request content."""

    @property
    def timeout(self) -> float | None:
        """Returns the timeout for the request in seconds, or None if not set."""

class SyncResponse:
    def __init__(
        self,
        *,
        status: int,
        http_version: HTTPVersion | None = None,
        headers: Headers | None = None,
        content: bytes | Iterable[bytes] | None = None,
        trailers: Headers | None = None,
    ) -> None:
        """Creates a new [`SyncResponse`][] object.

        Care must be taken if your service uses trailers and you override content.
        Trailers will not be received without fully consuming the original response content.
        Patterns that wrap the original response content should not have any issue but if
        you replace it completely and need trailers, make sure to still read and discard
        the original content.

        Args:
            status: The HTTP status code of the response.
            http_version: The HTTP version of the response.
            headers: The response headers.
            content: The response content.
            trailers: The response trailers.
        """

    def __enter__(self) -> SyncResponse:
        """Enters the context manager for the response to automatically
        close it when leaving.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exits the context manager for the response, closing it."""

    @property
    def status(self) -> int:
        """Returns the HTTP status code of the response."""

    @property
    def http_version(self) -> HTTPVersion:
        """Returns the HTTP version of the response."""

    @property
    def headers(self) -> Headers:
        """Returns the response headers."""

    @property
    def content(self) -> Iterator[bytes]:
        """Returns an iterator over the response content."""
    @property
    def trailers(self) -> Headers:
        """Returns the response trailers.

        Because trailers complete the response, this will only be filled after fully
        consuming the [`content`][] iterator.
        """

    def close(self) -> None:
        """Closes the response, releasing any underlying resources.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

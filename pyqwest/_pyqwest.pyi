import datetime
from collections.abc import (
    AsyncIterator,
    Awaitable,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    Sequence,
    ValuesView,
)
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Protocol, TypeAlias, TypeVar, overload, runtime_checkable

_T = TypeVar("_T")
JSON: TypeAlias = Mapping[str, JSON] | Sequence[JSON] | str | int | float | bool | None

Buffer: TypeAlias = bytes | memoryview | bytearray

class Headers:
    """Container of HTTP headers.

    This class behaves like a dictionary with case-insensitive keys and
    string values. Standard dictionary access will act as if keys can only
    have a single value. The add method can be used to It additionally can be used to store
    multiple values for the same key by using the add method. Iterating over
    values or items will return all values, including duplicates.
    """

    def __init__(
        self,
        items: Mapping[str | HTTPHeaderName, str]
        | Iterable[tuple[str | HTTPHeaderName, str]]
        | None = None,
    ) -> None:
        """Creates a new Headers object.

        Args:
            items: Initial headers to add.
        """

    def __getitem__(self, key: str | HTTPHeaderName) -> str:
        """Return the header value for the key.

        If multiple values are present for the key, returns the first value.

        Args:
            key: The header name.

        Raises:
            KeyError: If the key is not present.
        """

    def __setitem__(self, key: str | HTTPHeaderName, value: str) -> None:
        """Sets the header value for the key, replacing any existing values.

        Args:
            key: The header name.
            value: The header value.
        """

    def __delitem__(self, key: str | HTTPHeaderName) -> None:
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

    def __eq__(self, other: object) -> bool:
        """Compares the headers for equality with another Headers object,
        mapping, or iterable of key-value pairs.

        Args:
            other: The object to compare against.
        """

    def get(self, key: str | HTTPHeaderName, default: _T = None) -> str | _T:
        """Returns the header value for the key, or default if not present.

        Args:
            key: The header name.
            default: The default value to return if the key is not present.
        """

    @overload
    def pop(self, key: str | HTTPHeaderName) -> str:
        """Removes and returns the header value for the key.

        Args:
            key: The header name.

        Raises:
            KeyError: If the key is not present.
        """

    @overload
    def pop(self, key: str | HTTPHeaderName, default: _T) -> str | _T:
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

    def setdefault(self, key: str | HTTPHeaderName, default: str | None = None) -> str:
        """If the key is not present, sets it to the default value.
        Returns the value for the key.

        Args:
            key: The header name.
            default: The default value to set and return if the key is not present.
        """

    def add(self, key: str | HTTPHeaderName, value: str) -> None:
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
        self,
        items: Mapping[str | HTTPHeaderName, str]
        | Iterable[tuple[str | HTTPHeaderName, str]],
        /,
        **kwargs: str,
    ) -> None:
        """Updates headers with the provided items. Existing values are replaced.

        Args:
            items: Header names and values to set.
            **kwargs: Additional header names and values to set after items. May overwrite items.
        """

    def clear(self) -> None:
        """Removes all headers."""

    def getall(self, key: str | HTTPHeaderName) -> Sequence[str]:
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
    """An enumeration of HTTP versions."""

    HTTP1: HTTPVersion
    """HTTP/1.1"""

    HTTP2: HTTPVersion
    """HTTP/2"""

    HTTP3: HTTPVersion
    """HTTP/3"""

    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def __lt__(self, other: object) -> bool: ...
    def __le__(self, other: object) -> bool: ...
    def __gt__(self, other: object) -> bool: ...
    def __ge__(self, other: object) -> bool: ...

class Client:
    def __init__(self, transport: Transport | None = None) -> None:
        """Creates a new asynchronous HTTP client.

        The asynchronous client does not expose per-request timeouts on its methods.
        Use `asyncio.wait_for` or similar to enforce timeouts on requests.

        Args:
            transport: The transport to use for requests. If None, the shared default
                       transport will be used.
        """

    def get(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a GET HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def post(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a POST HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def delete(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a DELETE HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def head(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a HEAD HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def options(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a OPTIONS HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def patch(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a PATCH HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def put(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes a PUT HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Awaitable[FullResponse]:
        """Executes an HTTP request, returning the full buffered response.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def stream(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> Awaitable[Response]:
        """Executes an HTTP request, allowing the response content to be streamed.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

@runtime_checkable
class Transport(Protocol):
    """Protocol for asynchronous HTTP transport implementations.

    The default implementation of Transport is HTTPTransport which issues requests.
    Custom implementations may be useful to:

    - Mock requests for testing.
    - Add middleware wrapping transports
    """

    def execute(self, request: Request) -> Awaitable[Response]:
        """Executes a request."""

class HTTPTransport:
    """An HTTP transport implementation using reqwest."""

    def __init__(
        self,
        *,
        tls_ca_cert: bytes | None = None,
        tls_key: bytes | None = None,
        tls_cert: bytes | None = None,
        http_version: HTTPVersion | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = 30.0,
        read_timeout: float | None = None,
        pool_idle_timeout: float | None = 90.0,
        pool_max_idle_per_host: int | None = None,
        tcp_keepalive_interval: float | None = 30.0,
        enable_gzip: bool = True,
        enable_brotli: bool = True,
        enable_zstd: bool = True,
        use_system_dns: bool = False,
    ) -> None:
        """Creates a new HTTPTransport object.

        Without any arguments, the transport behaves like the default transport. When creating
        a transport, take care to set options to meet your needs.

        Args:
            tls_ca_cert: The CA certificate to use to verify the server for TLS connections.
            tls_key: The client private key to identify the client for mTLS connections.
                     tls_cert must also be set.
            tls_cert: The client certificate to identify the client for mTLS connections.
                      tls_key must also be set.
            http_version: The HTTP version to use for requests. If unset, HTTP/1 is used for
                          plaintext and ALPN negotiates the version for TLS connections
                          which typically means HTTP/2 if the server supports it.
            timeout: Default timeout for requests in seconds. This is the timeout from
                     the start of the request to the end of the response.
            connect_timeout: Timeout for connection establishment in seconds.
            read_timeout: Timeout for each read operation of a request in seconds.
            pool_idle_timeout: Timeout for idle connections in the connection pool in seconds.
            pool_max_idle_per_host: Maximum number of idle connections to keep in the pool per host.
                                    Defaults to 2.
            tcp_keepalive_interval: Interval for TCP keepalive probes in seconds.
            enable_gzip: Whether to enable gzip decompression for responses.
            enable_brotli: Whether to enable brotli decompression for responses.
            enable_zstd: Whether to enable zstd decompression for responses.
            use_system_dns: Whether to use the system DNS resolver. By default, pyqwest uses an
                            asynchronous DNS resolver implemented in Rust, but it can have different
                            behavior from system DNS in certain environments. Try enabling this option if
                            you have any DNS resolution issues.
        """

    def __aenter__(self) -> Awaitable[HTTPTransport]:
        """Enters the context manager for the transport to automatically close it when
        leaving.
        """

    def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Awaitable[None]:
        """Exits the context manager for the transport, closing it."""

    def execute(self, request: Request) -> Awaitable[Response]:
        """Executes the given request, returning the response.

        Args:
            request: The request to execute.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def aclose(self) -> Awaitable[None]:
        """Closes the transport, releasing any underlying resources."""

def get_default_transport() -> HTTPTransport:
    """Returns the singleton default HTTP transport instance used by clients that do not
    specify a transport.

    The default transport is constructed as follows:
    ```
    HTTPTransport(
        connect_timeout=30.0,
        pool_idle_timeout=90.0,
        tcp_keepalive_interval=30.0,
        enable_gzip: bool = True,
        enable_brotli: bool = True,
        enable_zstd: bool = True,
    )
    ```
    """

class Request:
    """An HTTP request."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> None:
        """Creates a new Request object.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
        """

    @property
    def method(self) -> str:
        """Returns the HTTP method of the request."""

    @property
    def url(self) -> str:
        """Returns the unencoded request URL."""

    @property
    def headers(self) -> Headers:
        """Returns the request headers."""

    @property
    def content(self) -> AsyncIterator[bytes]:
        """Returns an async iterator over the request content."""

class Response:
    """An HTTP response."""

    def __init__(
        self,
        *,
        status: int,
        http_version: HTTPVersion | None = None,
        headers: Headers | None = None,
        content: bytes | AsyncIterator[Buffer] | None = None,
        trailers: Headers | None = None,
    ) -> None:
        """Creates a new Response object.

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

    def __aenter__(self) -> Awaitable[Response]:
        """Enters the context manager for the response to automatically close it when
        leaving.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

    def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> Awaitable[None]:
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
    def content(self) -> AsyncIterator[Buffer]:
        """Returns an asynchronous iterator over the response content."""

    @property
    def trailers(self) -> Headers:
        """Returns the response trailers.

        Because trailers complete the response, this will only be filled after fully
        consuming the content iterator.
        """

    def aclose(self) -> Awaitable[None]:
        """Closes the response, releasing any underlying resources.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

class SyncClient:
    """A synchronous HTTP client.

    A client is a lightweight wrapper around a SyncTransport, providing convenience methods
    for common HTTP operations with buffering.
    """

    def __init__(self, transport: SyncTransport | None = None) -> None:
        """Creates a new synchronous HTTP client.

        Args:
            transport: The transport to use for requests. If None, the shared default
                       transport will be used.
        """

    def get(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a GET HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def post(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a POST HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def delete(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a DELETE HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def head(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a HEAD HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def options(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a OPTIONS HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def patch(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a PATCH HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def put(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes a PUT HTTP request.

        Args:
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> FullResponse:
        """Executes an HTTP request, returning the full buffered response.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

    def stream(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | Iterable[bytes] | None = None,
        timeout: float | None = None,
    ) -> AbstractContextManager[SyncResponse]:
        """Executes an HTTP request, allowing the response content to be streamed.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.

        Raises:
            ConnectionError: If the connection fails.
            TimeoutError: If the request times out.
            ReadError: If an error occurs reading the response.
            WriteError: If an error occurs writing the request.
        """

@runtime_checkable
class SyncTransport(Protocol):
    """Protocol for synchronous HTTP transport implementations.

    The default implementation of SyncTransport is SyncHTTPTransport which issues requests.
    Custom implementations may be useful to:

    - Mock requests for testing.
    - Add middleware wrapping transports
    """

    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        """Executes a request."""

class SyncHTTPTransport:
    """An HTTP transport implementation using reqwest."""

    def __init__(
        self,
        *,
        tls_ca_cert: bytes | None = None,
        tls_key: bytes | None = None,
        tls_cert: bytes | None = None,
        http_version: HTTPVersion | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = 30.0,
        read_timeout: float | None = None,
        pool_idle_timeout: float | None = 90.0,
        pool_max_idle_per_host: int | None = None,
        tcp_keepalive_interval: float | None = 30.0,
        enable_gzip: bool = True,
        enable_brotli: bool = True,
        enable_zstd: bool = True,
        use_system_dns: bool = False,
    ) -> None:
        """Creates a new SyncHTTPTransport object.

        Without any arguments, the transport behaves like the default transport. When creating
        a transport, take care to set options to meet your needs.

        Args:
            tls_ca_cert: The CA certificate to use to verify the server for TLS connections.
            tls_key: The client private key to identify the client for mTLS connections.
                     tls_cert must also be set.
            tls_cert: The client certificate to identify the client for mTLS connections.
                      tls_key must also be set.
            http_version: The HTTP version to use for requests. If unset, HTTP/1 is used for
                          plaintext and ALPN negotiates the version for TLS connections
                          which typically means HTTP/2 if the server supports it.
            timeout: Default timeout for requests in seconds. This is the timeout from
                     the start of the request to the end of the response.
            connect_timeout: Timeout for connection establishment in seconds.
            read_timeout: Timeout for each read operation of a request in seconds.
            pool_idle_timeout: Timeout for idle connections in the connection pool in seconds.
            pool_max_idle_per_host: Maximum number of idle connections to keep in the pool per host.
                                    Defaults to 2.
            tcp_keepalive_interval: Interval for TCP keepalive probes in seconds.
            enable_gzip: Whether to enable gzip decompression for responses.
            enable_brotli: Whether to enable brotli decompression for responses.
            enable_zstd: Whether to enable zstd decompression for responses.
            use_system_dns: Whether to use the system DNS resolver. By default, pyqwest uses an
                            asynchronous DNS resolver implemented in Rust, but it can have different
                            behavior from system DNS in certain environments. Try enabling this option if
                            you have any DNS resolution issues.
        """

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

    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        """Executes the given request, returning the response.

        Args:
            request: The request to execute.
        """

    def close(self) -> None:
        """Closes the transport, releasing any underlying resources."""

def get_default_sync_transport() -> SyncHTTPTransport:
    """Returns the singleton default HTTP transport instance used by synchronous clients that do not
    specify a transport.ult HTTP transport instance used by clients that do not
    specify a transport.

    The default transport is constructed as follows:
    ```
    SyncHTTPTransport(
        connect_timeout=30.0,
        pool_idle_timeout=90.0,
        tcp_keepalive_interval=30.0,
        enable_gzip: bool = True,
        enable_brotli: bool = True,
        enable_zstd: bool = True,
    )
    ```
    """

class SyncRequest:
    """An HTTP request."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: Headers | None = None,
        content: bytes | Iterable[bytes] | None = None,
    ) -> None:
        """Creates a new SyncRequest object.

        Args:
            method: The HTTP method.
            url: The unencoded request URL.
            headers: The request headers.
            content: The request content.
            timeout: The timeout for the request in seconds.
        """

    @property
    def method(self) -> str:
        """Returns the HTTP method of the request."""

    @property
    def url(self) -> str:
        """Returns the unencoded request URL."""

    @property
    def headers(self) -> Headers:
        """Returns the request headers."""

    @property
    def content(self) -> Iterator[bytes]:
        """Returns an iterator over the request content."""

class SyncResponse:
    """An HTTP response."""

    def __init__(
        self,
        *,
        status: int,
        http_version: HTTPVersion | None = None,
        headers: Headers | None = None,
        content: bytes | Iterable[Buffer] | None = None,
        trailers: Headers | None = None,
    ) -> None:
        """Creates a new SyncResponse object.

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
    def content(self) -> Iterator[Buffer]:
        """Returns an iterator over the response content."""

    @property
    def trailers(self) -> Headers:
        """Returns the response trailers.

        Because trailers complete the response, this will only be filled after fully
        consuming the content iterator.
        """

    def close(self) -> None:
        """Closes the response, releasing any underlying resources.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """

class FullResponse:
    """A fully buffered HTTP response."""

    def __init__(
        self, status: int, headers: Headers, content: bytes, trailers: Headers
    ) -> None:
        """Creates a new FullResponse object.

        Args:
            status: The HTTP status code of the response.
            headers: The response headers.
            content: The response content.
            trailers: The response trailers.
        """

    @property
    def status(self) -> int:
        """Returns the HTTP status code of the response."""

    @property
    def headers(self) -> Headers:
        """Returns the response headers."""

    @property
    def content(self) -> bytes:
        """Returns the response content."""

    @property
    def trailers(self) -> Headers:
        """Returns the response trailers."""

    def text(self) -> str:
        """Returns the response content decoded as text.

        The encoding for decoding is determined from the content-type header if present,
        defaulting to UTF-8 otherwise.
        """

    def json(self) -> JSON:
        """Parses and returns the response content as JSON.

        The content-type header is not checked when using this method.
        """

class StreamErrorCode:
    NO_ERROR: StreamErrorCode
    PROTOCOL_ERROR: StreamErrorCode
    INTERNAL_ERROR: StreamErrorCode
    FLOW_CONTROL_ERROR: StreamErrorCode
    SETTINGS_TIMEOUT: StreamErrorCode
    STREAM_CLOSED: StreamErrorCode
    FRAME_SIZE_ERROR: StreamErrorCode
    REFUSED_STREAM: StreamErrorCode
    CANCEL: StreamErrorCode
    COMPRESSION_ERROR: StreamErrorCode
    CONNECT_ERROR: StreamErrorCode
    ENHANCE_YOUR_CALM: StreamErrorCode
    INADEQUATE_SECURITY: StreamErrorCode
    HTTP_1_1_REQUIRED: StreamErrorCode

class StreamError(Exception):
    """An error representing an HTTP/2+ stream error."""

    def __init__(self, message: str, code: StreamErrorCode) -> None:
        """Creates a new StreamError.

        Args:
            message: The error message.
            code: The stream error code.
        """

    @property
    def code(self) -> StreamErrorCode:
        """The stream error code."""

class ReadError(Exception):
    """An error representing a read error during response reading."""

class WriteError(Exception):
    """An error representing a write error during request sending."""

class HTTPHeaderName:
    """An enum type corresponding to HTTP header names."""

    def __init__(self, name: str) -> None:
        """Creates a new HTTPHeaderName. When available, prefer one of the
        class attributes.

        Args:
            name: The header name.
        """

    ACCEPT: HTTPHeaderName
    """The "accept" header."""

    ACCEPT_CHARSET: HTTPHeaderName
    """The "accept-charset" header."""

    ACCEPT_ENCODING: HTTPHeaderName
    """The "accept-encoding" header."""

    ACCEPT_LANGUAGE: HTTPHeaderName
    """The "accept-language" header."""

    ACCEPT_RANGES: HTTPHeaderName
    """The "accept-ranges" header."""

    ACCESS_CONTROL_ALLOW_CREDENTIALS: HTTPHeaderName
    """The "access-control-allow-credentials" header."""

    ACCESS_CONTROL_ALLOW_HEADERS: HTTPHeaderName
    """The "access-control-allow-headers" header."""

    ACCESS_CONTROL_ALLOW_METHODS: HTTPHeaderName
    """The "access-control-allow-methods" header."""

    ACCESS_CONTROL_ALLOW_ORIGIN: HTTPHeaderName
    """The "access-control-allow-origin" header."""

    ACCESS_CONTROL_EXPOSE_HEADERS: HTTPHeaderName
    """The "access-control-expose-headers" header."""

    ACCESS_CONTROL_MAX_AGE: HTTPHeaderName
    """The "access-control-max-age" header."""

    ACCESS_CONTROL_REQUEST_HEADERS: HTTPHeaderName
    """The "access-control-request-headers" header."""

    ACCESS_CONTROL_REQUEST_METHOD: HTTPHeaderName
    """The "access-control-request-method" header."""

    AGE: HTTPHeaderName
    """The "age" header."""

    ALLOW: HTTPHeaderName
    """The "allow" header."""

    ALT_SVC: HTTPHeaderName
    """The "alt-svc" header."""

    AUTHORIZATION: HTTPHeaderName
    """The "authorization" header."""

    CACHE_CONTROL: HTTPHeaderName
    """The "cache-control" header."""

    CACHE_STATUS: HTTPHeaderName
    """The "cache-status" header."""

    CDN_CACHE_CONTROL: HTTPHeaderName
    """The "cdn-cache-control" header."""

    CONNECTION: HTTPHeaderName
    """The "connection" header."""

    CONTENT_DISPOSITION: HTTPHeaderName
    """The "content-disposition" header."""

    CONTENT_ENCODING: HTTPHeaderName
    """The "content-encoding" header."""

    CONTENT_LANGUAGE: HTTPHeaderName
    """The "content-language" header."""

    CONTENT_LENGTH: HTTPHeaderName
    """The "content-length" header."""

    CONTENT_LOCATION: HTTPHeaderName
    """The "content-location" header."""

    CONTENT_RANGE: HTTPHeaderName
    """The "content-range" header."""

    CONTENT_SECURITY_POLICY: HTTPHeaderName
    """The "content-security-policy" header."""

    CONTENT_SECURITY_POLICY_REPORT_ONLY: HTTPHeaderName
    """The "content-security-policy-report-only" header."""

    CONTENT_TYPE: HTTPHeaderName
    """The "content-type" header."""

    COOKIE: HTTPHeaderName
    """The "cookie" header."""

    DNT: HTTPHeaderName
    """The "dnt" header."""

    DATE: HTTPHeaderName
    """The "date" header."""

    ETAG: HTTPHeaderName
    """The "etag" header."""

    EXPECT: HTTPHeaderName
    """The "expect" header."""

    EXPIRES: HTTPHeaderName
    """The "expires" header."""

    FORWARDED: HTTPHeaderName
    """The "forwarded" header."""

    FROM: HTTPHeaderName
    """The "from" header."""

    HOST: HTTPHeaderName
    """The "host" header."""

    IF_MATCH: HTTPHeaderName
    """The "if-match" header."""

    IF_MODIFIED_SINCE: HTTPHeaderName
    """The "if-modified-since" header."""

    IF_NONE_MATCH: HTTPHeaderName
    """The "if-none-match" header."""

    IF_RANGE: HTTPHeaderName
    """The "if-range" header."""

    IF_UNMODIFIED_SINCE: HTTPHeaderName
    """The "if-unmodified-since" header."""

    LAST_MODIFIED: HTTPHeaderName
    """The "last-modified" header."""

    LINK: HTTPHeaderName
    """The "link" header."""

    LOCATION: HTTPHeaderName
    """The "location" header."""

    MAX_FORWARDS: HTTPHeaderName
    """The "max-forwards" header."""

    ORIGIN: HTTPHeaderName
    """The "origin" header."""

    PRAGMA: HTTPHeaderName
    """The "pragma" header."""

    PROXY_AUTHENTICATE: HTTPHeaderName
    """The "proxy-authenticate" header."""

    PROXY_AUTHORIZATION: HTTPHeaderName
    """The "proxy-authorization" header."""

    PUBLIC_KEY_PINS: HTTPHeaderName
    """The "public-key-pins" header."""

    PUBLIC_KEY_PINS_REPORT_ONLY: HTTPHeaderName
    """The "public-key-pins-report-only" header."""

    RANGE: HTTPHeaderName
    """The "range" header."""

    REFERER: HTTPHeaderName
    """The "referer" header."""

    REFERRER_POLICY: HTTPHeaderName
    """The "referrer-policy" header."""

    REFRESH: HTTPHeaderName
    """The "refresh" header."""

    RETRY_AFTER: HTTPHeaderName
    """The "retry-after" header."""

    SEC_WEBSOCKET_ACCEPT: HTTPHeaderName
    """The "sec-websocket-accept" header."""

    SEC_WEBSOCKET_EXTENSIONS: HTTPHeaderName
    """The "sec-websocket-extensions" header."""

    SEC_WEBSOCKET_KEY: HTTPHeaderName
    """The "sec-websocket-key" header."""

    SEC_WEBSOCKET_PROTOCOL: HTTPHeaderName
    """The "sec-websocket-protocol" header."""

    SEC_WEBSOCKET_VERSION: HTTPHeaderName
    """The "sec-websocket-version" header."""

    SERVER: HTTPHeaderName
    """The "server" header."""

    SET_COOKIE: HTTPHeaderName
    """The "set-cookie" header."""

    STRICT_TRANSPORT_SECURITY: HTTPHeaderName
    """The "strict-transport-security" header."""

    TE: HTTPHeaderName
    """The "te" header."""

    TRAILER: HTTPHeaderName
    """The "trailer" header."""

    TRANSFER_ENCODING: HTTPHeaderName
    """The "transfer-encoding" header."""

    USER_AGENT: HTTPHeaderName
    """The "user-agent" header."""

    UPGRADE: HTTPHeaderName
    """The "upgrade" header."""

    UPGRADE_INSECURE_REQUESTS: HTTPHeaderName
    """The "upgrade-insecure-requests" header."""

    VARY: HTTPHeaderName
    """The "vary" header."""

    VIA: HTTPHeaderName
    """The "via" header."""

    WARNING: HTTPHeaderName
    """The "warning" header."""

    WWW_AUTHENTICATE: HTTPHeaderName
    """The "www-authenticate" header."""

    X_CONTENT_TYPE_OPTIONS: HTTPHeaderName
    """The "x-content-type-options" header."""

    X_DNS_PREFETCH_CONTROL: HTTPHeaderName
    """The "x-dns-prefetch-control" header."""

    X_FRAME_OPTIONS: HTTPHeaderName
    """The "x-frame-options" header."""

    X_XSS_PROTECTION: HTTPHeaderName
    """The "x-xss-protection" header."""

def set_sync_timeout(timeout: float) -> AbstractContextManager[None]: ...
def get_sync_timeout() -> datetime.timedelta | None: ...

class _BrotliDecompressor:
    def feed(self, data: bytes, *, end: bool) -> bytes: ...

class _ZstdDecompressor:
    def feed(self, data: bytes, *, end: bool) -> bytes: ...

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from types import TracebackType

from .pyqwest import Client as NativeClient
from .pyqwest import FullResponse, Headers, HTTPVersion, Transport
from .pyqwest import Response as NativeResponse

# We expose plain-Python wrappers for the async methods as the easiest way
# of making them coroutines rather than methods that return Futures,
# which is more Pythonic.


class Client:
    """An asynchronous HTTP client.

    A client is a lightweight wrapper around a Transport, providing convenience methods
    for common HTTP operations with buffering.
    """

    _client: NativeClient

    def __init__(self, transport: Transport | None = None) -> None:
        """Creates a new asynchronous HTTP client.

        Args:
            transport: The transport to use for requests. If None, the shared default
                       transport will be used.
        """
        self._client = NativeClient(transport=transport)

    async def get(
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
        """
        return await self._client.get(url, headers=headers, timeout=timeout)

    async def post(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
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
        """
        return await self._client.post(
            url, headers=headers, content=content, timeout=timeout
        )

    async def delete(
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
        """
        return await self._client.delete(url, headers=headers, timeout=timeout)

    async def head(
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
        """
        return await self._client.head(url, headers=headers, timeout=timeout)

    async def options(
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
        """
        return await self._client.options(url, headers=headers, timeout=timeout)

    async def patch(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
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
        """
        return await self._client.patch(
            url, headers=headers, content=content, timeout=timeout
        )

    async def put(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
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
        """
        return await self._client.put(
            url, headers=headers, content=content, timeout=timeout
        )

    async def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
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
        """
        return await self._client.execute(
            method, url, headers=headers, content=content, timeout=timeout
        )

    async def stream(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
        timeout: float | None = None,
    ) -> Response:
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
        """
        native_response = await self._client.stream(
            method, url, headers=headers, content=content, timeout=timeout
        )
        response = Response.__new__(Response)
        response._response = native_response  # noqa: SLF001
        return response


class Response:
    """An HTTP response."""

    _response: NativeResponse

    def __init__(
        self,
        *,
        status: int,
        http_version: HTTPVersion | None = None,
        headers: Headers | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
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
        self._response = NativeResponse(
            status=status,
            http_version=http_version,
            headers=headers,
            content=content,
            trailers=trailers,
        )

    async def __aenter__(self) -> Response:
        """Enters the context manager for the response to automatically close it when
        leaving.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Exits the context manager for the response, closing it."""
        await self._response.__aexit__(_exc_type, _exc_value, _traceback)

    @property
    def status(self) -> int:
        """Returns the HTTP status code of the response."""
        return self._response.status

    @property
    def http_version(self) -> HTTPVersion:
        """Returns the HTTP version of the response."""
        return self._response.http_version

    @property
    def headers(self) -> Headers:
        """Returns the response headers."""
        return self._response.headers

    @property
    def content(self) -> AsyncIterator[bytes]:
        """Returns an asynchronous iterator over the response content."""
        return self._response.content

    @property
    def trailers(self) -> Headers:
        """Returns the response trailers.

        Because trailers complete the response, this will only be filled after fully
        consuming the content iterator.
        """
        return self._response.trailers

    async def read_full(self) -> FullResponse:
        """Reads the full response content, returning a FullResponse with it.

        After calling this method, the content iterator on this object will be empty.
        It is expected that this method is called to replace Response with FullResponse
        for full access to the response.
        """
        return await self._response.read_full()

    async def aclose(self) -> None:
        """Closes the response, releasing any underlying resources.

        Note that if your code is guaranteed to fully consume the response content,
        it is not necessary to explicitly close the response.
        """
        await self._response.aclose()

    @property
    def _read_pending(self) -> bool:
        return self._response._read_pending  # pyright: ignore[reportAttributeAccessIssue]  # noqa: SLF001

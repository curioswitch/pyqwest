from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from ._pyqwest import Client as NativeClient
from ._pyqwest import FullResponse, Headers, Response, Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping

# We expose plain-Python wrappers for the async methods as the easiest way
# of making them coroutines rather than methods that return Futures,
# which is more Pythonic.


class Client:
    """An asynchronous HTTP client.

    A client is a lightweight wrapper around a Transport, providing convenience methods
    for common HTTP operations with buffering.

    The asynchronous client does not expose per-request timeouts on its methods.
    Use `asyncio.wait_for` or similar to enforce timeouts per-requests or initialize
    `HTTPTransport` with a default timeout.
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
    ) -> FullResponse:
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
        return await self._client.get(url, headers=headers)

    async def post(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> FullResponse:
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
        return await self._client.post(url, headers=headers, content=content)

    async def delete(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> FullResponse:
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
        return await self._client.delete(url, headers=headers)

    async def head(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> FullResponse:
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
        return await self._client.head(url, headers=headers)

    async def options(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
    ) -> FullResponse:
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
        return await self._client.options(url, headers=headers)

    async def patch(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> FullResponse:
        """Executes a PATCH HTTP request.

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
        return await self._client.patch(url, headers=headers, content=content)

    async def put(
        self,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> FullResponse:
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
        return await self._client.put(url, headers=headers, content=content)

    async def execute(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> FullResponse:
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
        return await self._client.execute(method, url, headers=headers, content=content)

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        url: str,
        headers: Headers | Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
        content: bytes | AsyncIterator[bytes] | None = None,
    ) -> AsyncIterator[Response]:
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
        response = await self._client.stream(
            method, url, headers=headers, content=content
        )
        async with response:
            yield response

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from pyqwest import Transport
from pyqwest._pyqwest import Request, Response

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class LoggingTransport(Transport):
    """Logging middleware for async clients.

    Wrap a Transport with this class to log requests and responses to a standard
    library logger. Requests log at INFO, responses at INFO for statuses below 400 and
    ERROR otherwise, request failures at ERROR, and each received content chunk at DEBUG.

    The messages and levels can be customized by subclassing this class and overriding
    the `log_request`, `log_response`, `log_chunk`, and `log_error` methods.

    Examples:
        ```python
        import logging

        from pyqwest import Client, HTTPTransport
        from pyqwest.middleware.logging import LoggingTransport

        logger = logging.getLogger("myapp.http")
        client = Client(transport=LoggingTransport(HTTPTransport(), logger))
        await client.get("http://localhost/hello")  # logs the request and response
        ```
    """

    _transport: Transport
    _logger: logging.Logger

    def __init__(
        self, transport: Transport, logger: logging.Logger | None = None
    ) -> None:
        self._transport = transport
        self._logger = logger if logger is not None else logging.getLogger("pyqwest")

    @final
    async def execute(self, request: Request) -> Response:
        self.log_request(request)
        try:
            response = await self._transport.execute(request)
        except Exception as e:
            self.log_error(request, e)
            raise
        self.log_response(request, response)
        return Response(
            status=response.status,
            http_version=response.http_version,
            headers=response.headers,
            content=_LoggingContent(self, request, response),
            trailers=response.trailers,
        )

    def log_request(self, request: Request) -> None:
        self._logger.info("Request: %s %s", request.method, request.url)

    def log_response(self, request: Request, response: Response) -> None:
        level = logging.ERROR if response.status >= 400 else logging.INFO
        self._logger.log(
            level, "Response: %d %s %s", response.status, request.method, request.url
        )

    def log_chunk(
        self, request: Request, chunk: bytes | memoryview | bytearray
    ) -> None:
        self._logger.debug(
            "Response chunk: %d bytes %s %s", len(chunk), request.method, request.url
        )

    def log_error(self, request: Request, error: Exception) -> None:
        self._logger.error("Error: %r %s %s", error, request.method, request.url)


class _LoggingContent:
    def __init__(
        self, transport: LoggingTransport, request: Request, response: Response
    ) -> None:
        self._transport = transport
        self._request = request
        self._response = response
        self._iter = aiter(response.content)

    def __aiter__(self) -> AsyncIterator[bytes | memoryview | bytearray]:
        return self

    async def __anext__(self) -> bytes | memoryview | bytearray:
        try:
            chunk = await anext(self._iter)
        except StopAsyncIteration:
            raise
        except Exception as e:
            self._transport.log_error(self._request, e)
            raise
        self._transport.log_chunk(self._request, chunk)
        return chunk

    async def aclose(self) -> None:
        # Closing the wrapped response, not just its content iterator, releases the
        # underlying stream even when nothing was read yet.
        await self._response.aclose()

    @property
    def _read_pending(self) -> bool:
        return self._response._read_pending  # noqa: SLF001

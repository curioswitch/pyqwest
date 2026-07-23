from __future__ import annotations

import logging
from typing import TYPE_CHECKING, final

from pyqwest import SyncTransport
from pyqwest._pyqwest import SyncRequest, SyncResponse

if TYPE_CHECKING:
    from collections.abc import Iterator


class SyncLoggingTransport(SyncTransport):
    """Logging middleware for sync clients.

    Wrap a SyncTransport with this class to log requests and responses to a standard
    library logger. Requests log at INFO, responses at INFO for statuses below 400 and
    ERROR otherwise, request failures at ERROR, and each received content chunk at DEBUG.

    The messages and levels can be customized by subclassing this class and overriding
    the `log_request`, `log_response`, `log_chunk`, and `log_error` methods.

    Examples:
        ```python
        import logging

        from pyqwest import SyncClient, SyncHTTPTransport
        from pyqwest.middleware.logging import SyncLoggingTransport

        logger = logging.getLogger("myapp.http")
        client = SyncClient(transport=SyncLoggingTransport(SyncHTTPTransport(), logger))
        client.get("http://localhost/hello")  # logs the request and response
        ```
    """

    _transport: SyncTransport
    _logger: logging.Logger

    def __init__(
        self, transport: SyncTransport, logger: logging.Logger | None = None
    ) -> None:
        self._transport = transport
        self._logger = logger if logger is not None else logging.getLogger("pyqwest")

    @final
    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        self.log_request(request)
        try:
            response = self._transport.execute_sync(request)
        except Exception as e:
            self.log_error(request, e)
            raise
        self.log_response(request, response)
        return SyncResponse(
            status=response.status,
            http_version=response.http_version,
            headers=response.headers,
            content=_LoggingContent(self, request, response),
            trailers=response.trailers,
        )

    def log_request(self, request: SyncRequest) -> None:
        self._logger.info("Request: %s %s", request.method, request.url)

    def log_response(self, request: SyncRequest, response: SyncResponse) -> None:
        level = logging.ERROR if response.status >= 400 else logging.INFO
        self._logger.log(
            level, "Response: %d %s %s", response.status, request.method, request.url
        )

    def log_chunk(
        self, request: SyncRequest, chunk: bytes | memoryview | bytearray
    ) -> None:
        self._logger.debug(
            "Response chunk: %d bytes %s %s", len(chunk), request.method, request.url
        )

    def log_error(self, request: SyncRequest, error: Exception) -> None:
        self._logger.error("Error: %r %s %s", error, request.method, request.url)


class _LoggingContent:
    def __init__(
        self,
        transport: SyncLoggingTransport,
        request: SyncRequest,
        response: SyncResponse,
    ) -> None:
        self._transport = transport
        self._request = request
        self._response = response
        self._iter = iter(response.content)

    def __iter__(self) -> Iterator[bytes | memoryview | bytearray]:
        return self

    def __next__(self) -> bytes | memoryview | bytearray:
        try:
            chunk = next(self._iter)
        except StopIteration:
            raise
        except Exception as e:
            self._transport.log_error(self._request, e)
            raise
        self._transport.log_chunk(self._request, chunk)
        return chunk

    def close(self) -> None:
        # Closing the wrapped response, not just its content iterator, releases the
        # underlying stream even when nothing was read yet.
        self._response.close()

    @property
    def _read_pending(self) -> bool:
        return self._response._read_pending  # noqa: SLF001

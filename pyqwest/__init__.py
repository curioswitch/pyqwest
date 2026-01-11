from __future__ import annotations

__all__ = [
    "Client",
    "FullResponse",
    "HTTPStreamError",
    "HTTPStreamErrorCode",
    "HTTPTransport",
    "HTTPVersion",
    "Headers",
    "Request",
    "Response",
    "SyncClient",
    "SyncHTTPTransport",
    "SyncRequest",
    "SyncResponse",
    "SyncTransport",
    "Transport",
    "get_default_sync_transport",
    "get_default_transport",
]

from .pyqwest import (
    Client,
    FullResponse,
    Headers,
    HTTPStreamError,
    HTTPStreamErrorCode,
    HTTPTransport,
    HTTPVersion,
    Request,
    Response,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    Transport,
    get_default_sync_transport,
    get_default_transport,
)

__doc__ = pyqwest.__doc__  # noqa: F821

from __future__ import annotations

__all__ = [
    "Client",
    "FullResponse",
    "HTTPTransport",
    "HTTPVersion",
    "Headers",
    "ReadError",
    "Request",
    "Response",
    "StreamError",
    "StreamErrorCode",
    "SyncClient",
    "SyncHTTPTransport",
    "SyncRequest",
    "SyncResponse",
    "SyncTransport",
    "Transport",
    "WriteError",
    "get_default_sync_transport",
    "get_default_transport",
]

from ._coro import Client, Response
from .pyqwest import (
    FullResponse,
    Headers,
    HTTPTransport,
    HTTPVersion,
    ReadError,
    Request,
    StreamError,
    StreamErrorCode,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    Transport,
    WriteError,
    get_default_sync_transport,
    get_default_transport,
)

__doc__ = pyqwest.__doc__  # noqa: F821

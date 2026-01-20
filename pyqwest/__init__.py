from __future__ import annotations

__all__ = [
    "Client",
    "FullResponse",
    "HTTPHeaderName",
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

from . import _pyqwest
from ._coro import Client, Response
from ._pyqwest import (
    FullResponse,
    Headers,
    HTTPHeaderName,
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

__doc__ = _pyqwest.__doc__

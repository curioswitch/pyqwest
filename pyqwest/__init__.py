from __future__ import annotations

__all__ = [
    "Client",
    "ConnectTimeout",
    "FullResponse",
    "HTTPHeaderName",
    "HTTPTransport",
    "HTTPVersion",
    "Headers",
    "Multipart",
    "Part",
    "Proxy",
    "ReadError",
    "RemoteProtocolError",
    "Request",
    "Response",
    "StreamError",
    "StreamErrorCode",
    "SyncClient",
    "SyncHTTPTransport",
    "SyncMultipart",
    "SyncPart",
    "SyncRequest",
    "SyncResponse",
    "SyncTransport",
    "TooManyRedirects",
    "Transport",
    "WriteError",
    "get_default_sync_transport",
    "get_default_transport",
]

from . import _pyqwest
from ._coro import Client, Response
from ._errors import ConnectTimeout, RemoteProtocolError, StreamError, StreamErrorCode
from ._multipart import Multipart, Part, SyncMultipart, SyncPart
from ._pyqwest import (
    FullResponse,
    Headers,
    HTTPHeaderName,
    HTTPTransport,
    HTTPVersion,
    Proxy,
    ReadError,
    Request,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    TooManyRedirects,
    Transport,
    WriteError,
    get_default_sync_transport,
    get_default_transport,
)

__doc__ = _pyqwest.__doc__

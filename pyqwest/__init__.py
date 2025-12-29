from __future__ import annotations

__all__ = [
    "Client",
    "HTTPVersion",
    "Headers",
    "Request",
    "Response",
    "SyncClient",
    "SyncRequest",
    "SyncResponse",
]

from .pyqwest import (
    Client,
    Headers,
    HTTPVersion,
    Request,
    Response,
    SyncClient,
    SyncRequest,
    SyncResponse,
)

__doc__ = pyqwest.__doc__  # noqa: F821

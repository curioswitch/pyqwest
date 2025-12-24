from __future__ import annotations

__all__ = ["Client", "HTTPVersion", "Headers", "Request", "Response"]

from .pyqwest import Client, Headers, HTTPVersion, Request, Response

__doc__ = pyqwest.__doc__  # noqa: F821

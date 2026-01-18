from __future__ import annotations

from pyqwest import HTTPVersion


def test_http_version() -> None:
    assert str(HTTPVersion.HTTP1) == "HTTP/1.1"
    assert str(HTTPVersion.HTTP2) == "HTTP/2"
    assert str(HTTPVersion.HTTP3) == "HTTP/3"

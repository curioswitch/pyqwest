from __future__ import annotations

from pyqwest import HTTPVersion


def test_http_version() -> None:
    assert str(HTTPVersion.HTTP1) == "HTTP/1.1"
    assert HTTPVersion.HTTP1 == HTTPVersion.HTTP1
    assert HTTPVersion.HTTP1 < HTTPVersion.HTTP2
    assert HTTPVersion.HTTP1 < HTTPVersion.HTTP3
    assert repr(HTTPVersion.HTTP1) == "HTTPVersion.HTTP1"
    assert str(HTTPVersion.HTTP2) == "HTTP/2"
    assert HTTPVersion.HTTP2 <= HTTPVersion.HTTP2
    assert HTTPVersion.HTTP2 > HTTPVersion.HTTP1
    assert HTTPVersion.HTTP2 < HTTPVersion.HTTP3
    assert repr(HTTPVersion.HTTP2) == "HTTPVersion.HTTP2"
    assert str(HTTPVersion.HTTP3) == "HTTP/3"
    assert HTTPVersion.HTTP3 >= HTTPVersion.HTTP3
    assert HTTPVersion.HTTP3 > HTTPVersion.HTTP1
    assert HTTPVersion.HTTP3 > HTTPVersion.HTTP2
    assert repr(HTTPVersion.HTTP3) == "HTTPVersion.HTTP3"

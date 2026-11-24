from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from pyqwest import SyncClient
from pyqwest.testing import WSGITransport

from .apps.wsgi.kitchensink import app as kitchensink_app_wsgi

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from wsgiref.types import StartResponse, WSGIEnvironment


def test_no_start() -> None:
    transport = WSGITransport(kitchensink_app_wsgi)
    url = "http://localhost/no_start"
    client = SyncClient(transport)
    response = client.get(url)
    assert response.status == 500
    assert response.content == b"WSGI application did not call start_response"


def test_error_stream() -> None:
    transport = WSGITransport(kitchensink_app_wsgi)
    url = "http://localhost/error_stream"
    client = SyncClient(transport)
    response = client.get(url)
    assert response.status == 200
    assert response.content == b""
    assert transport.error_stream.getvalue() == "This is an error message\n"


def test_request_content_error_waits_for_app() -> None:
    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        try:
            environ["wsgi.input"].read()
        except Exception as e:
            # Error handling that takes time must finish before the client
            # call returns.
            time.sleep(0.1)
            print(f"handled: {e}", file=environ["wsgi.errors"])
            raise
        start_response("200 OK", [])
        return [b""]

    def request_content() -> Iterator[bytes]:
        yield b"Hello"
        raise error

    error = RuntimeError("Test error")
    transport = WSGITransport(app)
    client = SyncClient(transport)
    with pytest.raises(RuntimeError) as exc_info:
        client.post("http://localhost/", content=request_content())
    assert exc_info.value is error
    assert transport.app_exception is error
    assert transport.error_stream.getvalue() == "handled: Test error\n"

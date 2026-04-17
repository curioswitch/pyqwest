from __future__ import annotations

from pyqwest import SyncClient
from pyqwest.testing import WSGITransport

from .apps.wsgi.kitchensink import app as kitchensink_app_wsgi


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

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

import pytest

from pyqwest import SyncClient, SyncRequest
from pyqwest.middleware.logging import SyncLoggingTransport
from pyqwest.testing import WSGITransport

if TYPE_CHECKING:
    from collections.abc import Iterable

    if sys.version_info >= (3, 11):
        from wsgiref.types import StartResponse, WSGIEnvironment
    else:
        from _typeshed.wsgi import StartResponse, WSGIEnvironment


class App:
    def __init__(self):
        self.status = 200
        self.connection_error = False
        self.chunks = [b"hello", b"world"]

    def __call__(
        self, environ: WSGIEnvironment, start_response: StartResponse
    ) -> Iterable[bytes]:
        if self.connection_error:
            try:
                raise ConnectionError  # noqa: TRY301
            except ConnectionError:
                start_response("500 Internal Server Error", [], sys.exc_info())
            return []
        start_response(f"{self.status} {self.status}", [])
        return self.chunks


@pytest.fixture
def app():
    return App()


@pytest.fixture
def logger():
    return logging.getLogger("test-pyqwest-logging")


@pytest.fixture
def client(app: App, logger: logging.Logger):
    return SyncClient(SyncLoggingTransport(WSGITransport(app), logger))


def messages(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == logger.name
    ]


def test_success(
    client: SyncClient, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=logger.name)
    res = client.get("http://localhost/path")
    assert res.status == 200
    assert res.content == b"helloworld"
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/path"),
        (logging.INFO, "Response: 200 GET http://localhost/path"),
        (logging.DEBUG, "Response chunk: 5 bytes GET http://localhost/path"),
        (logging.DEBUG, "Response chunk: 5 bytes GET http://localhost/path"),
    ]


def test_error_status(
    app: App,
    client: SyncClient,
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    app.status = 500
    res = client.get("http://localhost")
    assert res.status == 500
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.ERROR, "Response: 500 GET http://localhost/"),
    ]


def test_connection_error(
    app: App,
    client: SyncClient,
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    app.connection_error = True
    with pytest.raises(ConnectionError):
        client.get("http://localhost")
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.ERROR, "Error: WSGIConnectionError('') GET http://localhost/"),
    ]


def test_no_chunks_without_debug(
    client: SyncClient, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    res = client.get("http://localhost")
    assert res.status == 200
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.INFO, "Response: 200 GET http://localhost/"),
    ]


def test_default_logger(app: App, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pyqwest")
    client = SyncClient(SyncLoggingTransport(WSGITransport(app)))
    res = client.get("http://localhost")
    assert res.status == 200
    assert [r.name for r in caplog.records if r.name == "pyqwest"] == [
        "pyqwest",
        "pyqwest",
    ]


def test_stream_close_before_read(
    client: SyncClient, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=logger.name)
    with client.stream("GET", "http://localhost"):
        pass
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.INFO, "Response: 200 GET http://localhost/"),
    ]


def test_custom_messages(
    app: App, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    class MyLoggingTransport(SyncLoggingTransport):
        def log_request(self, request: SyncRequest) -> None:
            self._logger.warning("Calling %s", request.url)

    caplog.set_level(logging.INFO, logger=logger.name)
    client = SyncClient(MyLoggingTransport(WSGITransport(app), logger))
    res = client.get("http://localhost")
    assert res.status == 200
    assert messages(caplog, logger) == [
        (logging.WARNING, "Calling http://localhost/"),
        (logging.INFO, "Response: 200 GET http://localhost/"),
    ]

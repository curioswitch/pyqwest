from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client
from pyqwest.middleware.logging import LoggingTransport
from pyqwest.testing import ASGITransport

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope


class App:
    def __init__(self):
        self.status = 200
        self.connection_error = False
        self.chunks = [b"hello", b"world"]

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scope["type"] != "http":
            return
        if self.connection_error:
            raise ConnectionError
        while True:
            message = await receive()
            if message["type"] == "http.request" and not message.get(
                "more_body", False
            ):
                break
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": [],
                "trailers": False,
            }
        )
        for i, chunk in enumerate(self.chunks):
            more_body = i < len(self.chunks) - 1
            await send(
                {"type": "http.response.body", "body": chunk, "more_body": more_body}
            )


@pytest.fixture
def app():
    return App()


@pytest.fixture
def logger():
    return logging.getLogger("test-pyqwest-logging-async")


@pytest.fixture
def client(app: App, logger: logging.Logger):
    return Client(LoggingTransport(ASGITransport(app), logger))


def messages(
    caplog: pytest.LogCaptureFixture, logger: logging.Logger
) -> list[tuple[int, str]]:
    return [
        (record.levelno, record.getMessage())
        for record in caplog.records
        if record.name == logger.name
    ]


@pytest.mark.asyncio
async def test_success(
    client: Client, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=logger.name)
    res = await client.get("http://localhost/path")
    assert res.status == 200
    assert res.content == b"helloworld"
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/path"),
        (logging.INFO, "Response: 200 GET http://localhost/path"),
        (logging.DEBUG, "Response chunk: 5 bytes GET http://localhost/path"),
        (logging.DEBUG, "Response chunk: 5 bytes GET http://localhost/path"),
    ]


@pytest.mark.asyncio
async def test_error_status(
    app: App, client: Client, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    app.status = 500
    res = await client.get("http://localhost")
    assert res.status == 500
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.ERROR, "Response: 500 GET http://localhost/"),
    ]


@pytest.mark.asyncio
async def test_connection_error(
    app: App, client: Client, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    app.connection_error = True
    with pytest.raises(ConnectionError):
        await client.get("http://localhost")
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.ERROR, "Error: ConnectionError() GET http://localhost/"),
    ]


@pytest.mark.asyncio
async def test_no_chunks_without_debug(
    client: Client, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=logger.name)
    res = await client.get("http://localhost")
    assert res.status == 200
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.INFO, "Response: 200 GET http://localhost/"),
    ]


@pytest.mark.asyncio
async def test_stream_close_before_read(
    client: Client, logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=logger.name)
    async with client.stream("GET", "http://localhost"):
        pass
    assert messages(caplog, logger) == [
        (logging.INFO, "Request: GET http://localhost/"),
        (logging.INFO, "Response: 200 GET http://localhost/"),
    ]

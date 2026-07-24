from __future__ import annotations

import asyncio
import logging
import socket

import pytest

from pyqwest import Client, HTTPVersion, SyncClient

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True),
    pytest.mark.parametrize("client_type", ["async", "sync"]),
]

DEBUG_LOGGER = "pyqwest"
ACCESS_LOGGER = "pyqwest.access"


def version_display(http_version: HTTPVersion | None) -> str:
    return "HTTP/2" if http_version == HTTPVersion.HTTP2 else "HTTP/1.1"


def records_for(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == name]


async def _get(client: Client | SyncClient, url: str):
    if isinstance(client, SyncClient):
        return await asyncio.to_thread(client.get, url)
    return await client.get(url)


@pytest.mark.asyncio
async def test_access_log(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = f"{url}/echo"
    with caplog.at_level(logging.DEBUG, logger=ACCESS_LOGGER):
        resp = await _get(client, url)
    assert resp.status == 200

    records = records_for(caplog, ACCESS_LOGGER)
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.DEBUG
    version = version_display(http_version)
    assert record.getMessage() == f'HTTP Request: GET {url} "{version} 200 OK"'
    assert record.args == ("GET", url, version, 200, "OK")
    # Enabling the access child does not enable the parent "pyqwest" logger.
    assert not records_for(caplog, DEBUG_LOGGER)


@pytest.mark.asyncio
async def test_access_log_error_status(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = f"{url}/not-found"
    with caplog.at_level(logging.DEBUG, logger=ACCESS_LOGGER):
        resp = await _get(client, url)
    assert resp.status == 404

    records = records_for(caplog, ACCESS_LOGGER)
    assert len(records) == 1
    version = version_display(http_version)
    assert (
        records[0].getMessage() == f'HTTP Request: GET {url} "{version} 404 Not Found"'
    )


@pytest.mark.asyncio
async def test_debug_log(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = f"{url}/echo"
    with caplog.at_level(logging.DEBUG, logger=DEBUG_LOGGER):
        resp = await _get(client, url)
    assert resp.status == 200

    records = records_for(caplog, DEBUG_LOGGER)
    assert [record.getMessage() for record in records] == [
        f"Sending HTTP request: GET {url}"
    ]
    assert records[0].levelno == logging.DEBUG
    # The access logger inherits DEBUG from its "pyqwest" parent, completing the
    # request lifecycle without duplicated records.
    access_records = records_for(caplog, ACCESS_LOGGER)
    assert len(access_records) == 1
    version = version_display(http_version)
    assert (
        access_records[0].getMessage() == f'HTTP Request: GET {url} "{version} 200 OK"'
    )


@pytest.mark.asyncio
async def test_debug_log_with_access_overridden(
    client: Client | SyncClient, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    url = f"{url}/echo"
    access_logger = logging.getLogger(ACCESS_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=DEBUG_LOGGER):
        access_logger.setLevel(logging.INFO)
        try:
            resp = await _get(client, url)
        finally:
            access_logger.setLevel(logging.NOTSET)
    assert resp.status == 200

    # An explicit level on the access child overrides the inherited DEBUG.
    assert not records_for(caplog, ACCESS_LOGGER)
    records = records_for(caplog, DEBUG_LOGGER)
    assert [record.getMessage() for record in records] == [
        f"Sending HTTP request: GET {url}"
    ]


@pytest.mark.asyncio
async def test_stream_logged(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    url = f"{url}/echo"
    with caplog.at_level(logging.DEBUG, logger=ACCESS_LOGGER):
        if isinstance(client, SyncClient):

            def run() -> bytes:
                with client.stream("POST", url, content=b"Hello, World!") as resp:
                    return b"".join(resp.content)

            content = await asyncio.to_thread(run)
        else:
            async with client.stream("POST", url, content=b"Hello, World!") as resp:
                content = b""
                async for chunk in resp.content:
                    content += chunk
    assert content == b"Hello, World!"

    records = records_for(caplog, ACCESS_LOGGER)
    assert len(records) == 1
    version = version_display(http_version)
    assert records[0].getMessage() == f'HTTP Request: POST {url} "{version} 200 OK"'


@pytest.mark.asyncio
async def test_not_logged_at_info(
    client: Client | SyncClient, url: str, caplog: pytest.LogCaptureFixture
) -> None:
    url = f"{url}/echo"
    with (
        caplog.at_level(logging.INFO, logger=DEBUG_LOGGER),
        caplog.at_level(logging.INFO, logger=ACCESS_LOGGER),
    ):
        resp = await _get(client, url)
    assert resp.status == 200
    assert not records_for(caplog, DEBUG_LOGGER)
    assert not records_for(caplog, ACCESS_LOGGER)


@pytest.mark.asyncio
async def test_connection_error(
    client: Client | SyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://localhost:{port}/echo"
    with (
        caplog.at_level(logging.DEBUG, logger=DEBUG_LOGGER),
        caplog.at_level(logging.DEBUG, logger=ACCESS_LOGGER),
        pytest.raises(ConnectionError),
    ):
        await _get(client, url)

    # No response was received so nothing is access logged, matching httpx.
    assert not records_for(caplog, ACCESS_LOGGER)
    records = records_for(caplog, DEBUG_LOGGER)
    assert len(records) == 2
    assert records[0].getMessage() == f"Sending HTTP request: GET {url}"
    assert records[1].getMessage().startswith(f"HTTP request failed: GET {url} (")

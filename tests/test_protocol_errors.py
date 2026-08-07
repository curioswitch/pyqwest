from __future__ import annotations

import asyncio

import pytest

from pyqwest import (
    Client,
    HTTPTransport,
    ReadError,
    RemoteProtocolError,
    SyncClient,
    SyncHTTPTransport,
    WriteError,
)

from ._util import (
    BAD_CHUNKED_FRAMING,
    GARBAGE_RESPONSE,
    MALFORMED_STATUS_LINE,
    NO_RESPONSE,
    TRUNCATED_CHUNKED_RESPONSE,
    TRUNCATED_RESPONSE,
    raw_server,
)

# Each of these is a way for the peer to break HTTP framing, and each is served
# over a raw socket so the fault is exactly what the test names. They are
# HTTP/1.1 by construction, so unlike the tests above they build their own
# transport rather than using the parametrized fixtures.
PROTOCOL_FAULTS = [
    pytest.param(NO_RESPONSE, id="no-response"),
    pytest.param(MALFORMED_STATUS_LINE, id="malformed-status-line"),
    pytest.param(GARBAGE_RESPONSE, id="garbage-response"),
    pytest.param(BAD_CHUNKED_FRAMING, id="bad-chunked-framing"),
    pytest.param(TRUNCATED_RESPONSE, id="truncated-body"),
    pytest.param(TRUNCATED_CHUNKED_RESPONSE, id="truncated-chunked-body"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("response", PROTOCOL_FAULTS)
async def test_async_protocol_error(response: bytes) -> None:
    with raw_server(response) as url:
        async with HTTPTransport() as transport:
            with pytest.raises(RemoteProtocolError):
                await Client(transport).get(url)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", PROTOCOL_FAULTS)
async def test_sync_protocol_error(response: bytes) -> None:
    def run() -> None:
        with (
            raw_server(response) as url,
            SyncHTTPTransport() as transport,
            pytest.raises(RemoteProtocolError),
        ):
            SyncClient(transport).get(url)

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_async_response_reset_is_read_error() -> None:
    # A response cut short by a reset is a broken connection rather than a
    # protocol violation, so it must not be swept up by the tests above.
    with raw_server(TRUNCATED_RESPONSE, reset=True) as url:
        async with HTTPTransport() as transport:
            # Usually a read error but timing can make it a write error,
            # especially on Windows.
            with pytest.raises((ReadError, WriteError)):
                await Client(transport).get(url)


@pytest.mark.asyncio
async def test_sync_response_reset_is_read_error() -> None:
    def run() -> None:
        with (
            raw_server(TRUNCATED_RESPONSE, reset=True) as url,
            SyncHTTPTransport() as transport,
            pytest.raises((ReadError, WriteError)),
        ):
            SyncClient(transport).get(url)

    await asyncio.to_thread(run)

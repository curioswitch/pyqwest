from __future__ import annotations

import asyncio

import pytest

from pyqwest import (
    Client,
    Request,
    SyncClient,
    SyncRequest,
    get_default_sync_transport,
    get_default_transport,
)

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


@pytest.mark.asyncio
async def test_default_transport(url: str) -> None:
    transport = get_default_transport()
    url = f"{url}/echo"
    res = await transport.execute(Request("GET", url))
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_sync_transport(url: str) -> None:
    transport = get_default_sync_transport()
    url = f"{url}/echo"
    res = await asyncio.to_thread(transport.execute, SyncRequest("GET", url))
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_client(url: str) -> None:
    client = Client()
    url = f"{url}/echo"
    res = await client.get(url)
    assert res.status == 200
    assert res.content == b""


@pytest.mark.asyncio
async def test_default_sync_client(url: str) -> None:
    client = SyncClient()
    url = f"{url}/echo"
    res = await asyncio.to_thread(client.get, url)
    assert res.status == 200
    assert res.content == b""

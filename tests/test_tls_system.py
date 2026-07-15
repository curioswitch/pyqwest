from __future__ import annotations

import asyncio

import pytest

from pyqwest import Client, SyncClient


@pytest.mark.asyncio
async def test_default_sync_client_tls() -> None:
    client = SyncClient()
    url = "https://pyqwest.dev/objects.inv"
    res = await asyncio.to_thread(client.get, url)
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_client_tls() -> None:
    client = Client()
    url = "https://pyqwest.dev/objects.inv"
    res = await client.get(url)
    assert res.status == 200

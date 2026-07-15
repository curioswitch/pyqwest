from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client, HTTPTransport, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from pyvoy import PyvoyServer

    from .conftest import Certs


@pytest.mark.asyncio
async def test_default_client() -> None:
    client = Client()
    url = "https://pyqwest.dev/objects.inv"
    res = await client.get(url)
    assert res.status == 200


@pytest.mark.asyncio
async def test_default_sync_client() -> None:
    client = SyncClient()
    url = "https://pyqwest.dev/objects.inv"
    res = await asyncio.to_thread(client.get, url)
    assert res.status == 200


@pytest.mark.asyncio
async def test_empty_transport() -> None:
    async with HTTPTransport() as transport:
        client = Client(transport)
        url = "https://pyqwest.dev/objects.inv"
        with pytest.raises(ConnectionError):
            await client.get(url)


@pytest.mark.asyncio
async def test_empty_sync_transport() -> None:
    with SyncHTTPTransport() as transport:
        client = SyncClient(transport)
        url = "https://pyqwest.dev/objects.inv"
        with pytest.raises(ConnectionError):
            await asyncio.to_thread(client.get, url)


@pytest.mark.asyncio
async def test_transport_include_system_certs() -> None:
    async with HTTPTransport(tls_include_system_certs=True) as transport:
        client = Client(transport)
        url = "https://pyqwest.dev/objects.inv"
        res = await client.get(url)
        assert res.status == 200


@pytest.mark.asyncio
async def test_sync_transport_include_system_certs() -> None:
    with SyncHTTPTransport(tls_include_system_certs=True) as transport:
        client = SyncClient(transport)
        url = "https://pyqwest.dev/objects.inv"
        res = await asyncio.to_thread(client.get, url)
        assert res.status == 200


@pytest.mark.asyncio
async def test_transport_ca_cert_and_include_system_certs(
    certs: Certs, server: PyvoyServer, subtests: pytest.Subtests
) -> None:
    async with HTTPTransport(
        tls_ca_cert=certs.ca, tls_include_system_certs=True
    ) as transport:
        client = Client(transport)
        with subtests.test("system cert"):
            url = "https://pyqwest.dev/objects.inv"
            res = await client.get(url)
            assert res.status == 200

        with subtests.test("custom cert"):
            url = f"https://localhost:{server.listener_port_tls}/echo"
            res = await client.get(url)
            assert res.status == 200


@pytest.mark.asyncio
async def test_sync_transport_ca_cert_and_include_system_certs(
    certs: Certs, server: PyvoyServer, subtests: pytest.Subtests
) -> None:
    with SyncHTTPTransport(
        tls_ca_cert=certs.ca, tls_include_system_certs=True
    ) as transport:
        client = SyncClient(transport)
        with subtests.test("system cert"):
            url = "https://pyqwest.dev/objects.inv"
            res = await asyncio.to_thread(client.get, url)
            assert res.status == 200

        with subtests.test("custom cert"):
            url = f"https://localhost:{server.listener_port_tls}/echo"
            res = await asyncio.to_thread(client.get, url)
            assert res.status == 200

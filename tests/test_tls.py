from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyqwest import Client, HTTPTransport, HTTPVersion, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    import trustme

    from .conftest import Certs


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


@pytest.mark.asyncio
async def test_mtls(
    url: str,
    certs: Certs,
    http_version: HTTPVersion | None,
    ca: trustme.CA,
    client_type: str,
) -> None:
    client_cert = ca.issue_cert(
        common_name="someclient",
        organization_name="curioswitch",
        organization_unit_name="tests",
    )

    method = "POST"
    url = f"{url}/echo"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"

    if client_type == "sync":
        with SyncHTTPTransport(
            tls_ca_cert=certs.ca,
            tls_key=client_cert.private_key_pem.bytes(),
            tls_cert=client_cert.cert_chain_pems[0].bytes(),
            http_version=http_version,
        ) as transport:
            client = SyncClient(transport)

            def run():
                with client.stream(method, url, headers, req_content) as resp:
                    content = b"".join(resp.content)
                return resp, content

            resp, content = await asyncio.to_thread(run)
    else:
        async with HTTPTransport(
            tls_ca_cert=certs.ca,
            tls_key=client_cert.private_key_pem.bytes(),
            tls_cert=client_cert.cert_chain_pems[0].bytes(),
            http_version=http_version,
        ) as transport:
            client = Client(transport)
            async with client.stream(method, url, headers, req_content) as resp:
                content = b""
                async for chunk in resp.content:
                    content += chunk

    assert resp.status == 200
    assert (
        resp.headers["x-echo-tls-client-name"] == "CN=someclient,OU=tests,O=curioswitch"
    )
    assert content == b"Hello, World!"

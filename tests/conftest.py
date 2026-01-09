from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import trustme
from pyvoy import PyvoyServer

from pyqwest import Client, HTTPTransport, HTTPVersion, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@dataclass
class Certs:
    ca: bytes
    server_cert: bytes
    server_key: bytes


@pytest.fixture(scope="session")
def ca() -> trustme.CA:
    return trustme.CA()


@pytest.fixture(scope="session")
def certs(ca: trustme.CA) -> Certs:
    # Workaround https://github.com/seanmonstar/reqwest/issues/2911
    server = ca.issue_cert("localhost")
    return Certs(
        ca=ca.cert_pem.bytes(),
        server_cert=server.cert_chain_pems[0].bytes(),
        server_key=server.private_key_pem.bytes(),
    )


@pytest_asyncio.fixture(scope="session")
async def server(certs: Certs) -> AsyncIterator[PyvoyServer]:
    # TODO: Fix issue in pyvoy where if tls_port is 0, separate ports are picked for
    # TLS and QUIC and we cannot find the latter.
    tls_port = 0
    while tls_port <= 0:
        with socket.socket() as s:
            s.bind(("", 0))
            tls_port = s.getsockname()[1]
    async with PyvoyServer(
        "tests.apps.asgi.kitchensink",
        tls_port=tls_port,
        tls_key=certs.server_key,
        tls_cert=certs.server_cert,
        tls_ca_cert=certs.ca,
        tls_require_client_certificate=False,
        lifespan=False,
        stdout=None,
        stderr=None,
    ) as server:
        yield server


@pytest.fixture(scope="session")
def http_scheme(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture(scope="session")
def http_version(request: pytest.FixtureRequest) -> HTTPVersion | None:
    match request.param:
        case "h1":
            return HTTPVersion.HTTP1
        case "h2":
            return HTTPVersion.HTTP2
        case "h3":
            return HTTPVersion.HTTP3
        case "auto":
            return None
        case _:
            msg = "Invalid HTTP version"
            raise ValueError(msg)


@pytest.fixture
def url(server: PyvoyServer, http_scheme: str, http_version: HTTPVersion | None) -> str:
    match http_scheme:
        case "http":
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("HTTP/3 over plain HTTP is not supported")
            return f"http://localhost:{server.listener_port}"
        case "https":
            return f"https://localhost:{server.listener_port_tls}"
        case _:
            msg = "Invalid scheme"
            raise ValueError(msg)


@pytest_asyncio.fixture(scope="session")
async def async_transport(
    certs: Certs, http_version: HTTPVersion | None
) -> AsyncIterator[HTTPTransport]:
    async with HTTPTransport(
        tls_ca_cert=certs.ca, http_version=http_version
    ) as transport:
        yield transport


@pytest.fixture(scope="session")
def async_client(async_transport: HTTPTransport) -> Client:
    return Client(async_transport)


@pytest.fixture(scope="session")
def sync_transport(
    certs: Certs, http_version: HTTPVersion | None
) -> Iterator[SyncHTTPTransport]:
    with SyncHTTPTransport(
        tls_ca_cert=certs.ca, http_version=http_version
    ) as transport:
        yield transport


@pytest.fixture(scope="session")
def sync_client(sync_transport: SyncHTTPTransport) -> SyncClient:
    return SyncClient(sync_transport)


@pytest.fixture(params=["async", "sync"])
def client_type(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def transport(
    async_transport: HTTPTransport, sync_transport: SyncHTTPTransport, client_type: str
) -> HTTPTransport | SyncHTTPTransport:
    match client_type:
        case "async":
            return async_transport
        case "sync":
            return sync_transport
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture
def client(
    async_client: Client, sync_client: SyncClient, client_type: str
) -> Client | SyncClient:
    match client_type:
        case "async":
            return async_client
        case "sync":
            return sync_client
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)

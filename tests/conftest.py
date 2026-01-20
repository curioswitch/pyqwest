from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import trustme
from pyvoy import PyvoyServer

from pyqwest import (
    Client,
    HTTPTransport,
    HTTPVersion,
    SyncClient,
    SyncHTTPTransport,
    SyncTransport,
    Transport,
)
from pyqwest.testing import ASGITransport, WSGITransport

from .apps.asgi.kitchensink import app as kitchensink_app_asgi
from .apps.wsgi.kitchensink import app as kitchensink_app_wsgi

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
    async with PyvoyServer(
        "tests.apps.asgi.kitchensink",
        tls_port=0,
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
            return f"https://localhost:{server.listener_port_tls if http_version != HTTPVersion.HTTP3 else server.listener_port_quic}"
        case _:
            msg = "Invalid scheme"
            raise ValueError(msg)


@pytest_asyncio.fixture(scope="session")
async def async_transport(
    certs: Certs, http_version: HTTPVersion | None
) -> AsyncIterator[HTTPTransport]:
    async with HTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        enable_brotli=True,
        enable_gzip=True,
        enable_zstd=True,
    ) as transport:
        yield transport


@pytest_asyncio.fixture(scope="session")
async def async_asgi_transport(
    http_version: HTTPVersion | None, http_scheme: str
) -> AsyncIterator[Transport]:
    if not http_version:
        match http_scheme:
            case "https":
                http_version = HTTPVersion.HTTP2
            case _:
                http_version = HTTPVersion.HTTP1
    async with ASGITransport(
        kitchensink_app_asgi, http_version=http_version
    ) as transport:
        yield transport


@pytest.fixture(scope="session", params=["async", "async_asgi"])
def async_client(
    request: pytest.FixtureRequest,
    async_transport: HTTPTransport,
    async_asgi_transport: Transport,
) -> Client:
    match request.param:
        case "async":
            return Client(async_transport)
        case "async_asgi":
            return Client(async_asgi_transport)
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture(scope="session")
def sync_transport(
    certs: Certs, http_version: HTTPVersion | None
) -> Iterator[SyncHTTPTransport]:
    with SyncHTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        enable_brotli=True,
        enable_gzip=True,
        enable_zstd=True,
    ) as transport:
        yield transport


@pytest.fixture(scope="session")
def sync_wsgi_transport(
    http_version: HTTPVersion | None, http_scheme: str
) -> SyncTransport:
    if not http_version:
        match http_scheme:
            case "https":
                http_version = HTTPVersion.HTTP2
            case _:
                http_version = HTTPVersion.HTTP1
    return WSGITransport(kitchensink_app_wsgi, http_version=http_version)


@pytest.fixture(scope="session", params=["sync", "sync_wsgi"])
def sync_client(
    request: pytest.FixtureRequest,
    sync_transport: SyncHTTPTransport,
    sync_wsgi_transport: SyncTransport,
) -> SyncClient:
    match request.param:
        case "sync":
            return SyncClient(sync_transport)
        case "sync_wsgi":
            return SyncClient(sync_wsgi_transport)
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture(params=["async", "sync", "async_asgi", "sync_wsgi"])
def client_type(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def transport(
    async_transport: HTTPTransport,
    sync_transport: SyncHTTPTransport,
    async_asgi_transport: Transport,
    sync_wsgi_transport: SyncTransport,
    client_type: str,
) -> HTTPTransport | SyncHTTPTransport | Transport | SyncTransport:
    match client_type:
        case "async":
            return async_transport
        case "sync":
            return sync_transport
        case "async_asgi":
            return async_asgi_transport
        case "sync_wsgi":
            return sync_wsgi_transport
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture
def client(
    async_transport: HTTPTransport,
    sync_transport: SyncHTTPTransport,
    async_asgi_transport: Transport,
    sync_wsgi_transport: SyncTransport,
    client_type: str,
) -> Client | SyncClient:
    match client_type:
        case "async":
            return Client(async_transport)
        case "sync":
            return SyncClient(sync_transport)
        case "async_asgi":
            return Client(async_asgi_transport)
        case "sync_wsgi":
            return SyncClient(sync_wsgi_transport)
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)

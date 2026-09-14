from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
import sniffio
import trustme
from opentelemetry.test.test_base import TestBase
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


# anyio's asyncio runner installs its own loop exception handler and re-raises
# what callbacks, tasks and futures left unhandled into the running test, so no
# tracking of loop exceptions is needed here.


# The backends async tests run on, each with one runner for the whole session.
@pytest.fixture(scope="session", params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return request.param


# anyio keeps one runner and hands it to any backend while a fixture from the
# previous backend is still alive, which would silently run the following tests
# on that backend. So session async fixtures depend on `anyio_backend`, which
# tears them down at a backend switch, and this checks that nothing outlived it.
@pytest.fixture(autouse=True)
def no_stale_runner(request: pytest.FixtureRequest) -> None:
    callspec = getattr(request.node, "callspec", None)
    expected = callspec.params.get("anyio_backend") if callspec is not None else None
    if expected is None:
        return
    try:
        running = sniffio.current_async_library()
    except sniffio.AsyncLibraryNotFoundError:  # no runner alive; anyio starts one
        return
    if running != expected:
        pytest.fail(f"a {running} runner is still alive for this {expected} test")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Deselects `asyncio_only` tests on the other backends."""
    kept: list[pytest.Item] = []
    dropped: list[pytest.Item] = []
    for item in items:
        callspec = getattr(item, "callspec", None)
        backend = callspec.params.get("anyio_backend") if callspec is not None else None
        if backend not in (None, "asyncio") and item.get_closest_marker("asyncio_only"):
            dropped.append(item)
        else:
            kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept


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


@pytest.fixture(scope="session")
def server(certs: Certs) -> Iterator[PyvoyServer]:
    server = PyvoyServer(
        "tests.apps.asgi.kitchensink",
        tls_port=0,
        tls_key=certs.server_key,
        tls_cert=certs.server_cert,
        tls_ca_cert=certs.ca,
        tls_require_client_certificate=False,
        lifespan=False,
        stdout=None,
        stderr=None,
    )
    # pyvoy drives its Envoy subprocess with asyncio. A private loop keeps the
    # server independent of the backend the async tests run on.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(server.start())
        try:
            yield server
        finally:
            loop.run_until_complete(server.stop())
    finally:
        loop.close()


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
def server_port(
    server: PyvoyServer, http_scheme: str, http_version: HTTPVersion | None
) -> int:
    match http_scheme:
        case "http":
            if http_version == HTTPVersion.HTTP3:
                pytest.skip("HTTP/3 over plain HTTP is not supported")
            return server.listener_port
        case "https":
            port = (
                server.listener_port_tls
                if http_version != HTTPVersion.HTTP3
                else server.listener_port_quic
            )
            assert port is not None  # noqa: S101
            return port
        case _:
            msg = "Invalid scheme"
            raise ValueError(msg)


@pytest.fixture
def url(server_port: int, http_scheme: str) -> str:
    return f"{http_scheme}://localhost:{server_port}"


@pytest.fixture(scope="session")
def otel_test_base() -> Iterator[TestBase]:
    test_base = TestBase()
    test_base.setUp()
    try:
        yield test_base
    finally:
        test_base.tearDown()


@pytest.fixture(scope="session")
async def async_transport(
    anyio_backend: object,  # noqa: ARG001  # torn down when the backend changes
    certs: Certs,
    http_version: HTTPVersion | None,
    otel_test_base: TestBase,  # noqa: ARG001
) -> AsyncIterator[HTTPTransport]:
    async with HTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        enable_brotli=True,
        enable_gzip=True,
        enable_zstd=True,
    ) as transport:
        yield transport


@pytest.fixture(scope="session")
async def async_asgi_transport(
    anyio_backend: str, http_version: HTTPVersion | None, http_scheme: str
) -> AsyncIterator[Transport | None]:
    if anyio_backend != "asyncio":
        # asyncio-only; every test that would use it is deselected. It stays a
        # regular dependency of the client fixtures so they are torn down with
        # it, which a `getfixturevalue` would not arrange.
        yield None
        return
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


def asgi_transport(transport: Transport | None) -> Transport:
    if transport is None:
        pytest.fail("the ASGI testing transport runs only on asyncio")
    return transport


@pytest.fixture(
    scope="session",
    params=["async", pytest.param("async_asgi", marks=pytest.mark.asyncio_only)],
)
def async_client(
    request: pytest.FixtureRequest,
    async_transport: HTTPTransport,
    async_asgi_transport: Transport | None,
) -> Client:
    match request.param:
        case "async":
            return Client(async_transport)
        case "async_asgi":
            return Client(asgi_transport(async_asgi_transport))
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture(scope="session")
def sync_transport(
    certs: Certs,
    http_version: HTTPVersion | None,
    otel_test_base: TestBase,  # noqa: ARG001
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


@pytest.fixture(
    params=[
        "async",
        "sync",
        pytest.param("async_asgi", marks=pytest.mark.asyncio_only),
        "sync_wsgi",
    ]
)
def client_type(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def transport(
    async_transport: HTTPTransport,
    sync_transport: SyncHTTPTransport,
    async_asgi_transport: Transport | None,
    sync_wsgi_transport: SyncTransport,
    client_type: str,
) -> HTTPTransport | SyncHTTPTransport | Transport | SyncTransport:
    match client_type:
        case "async":
            return async_transport
        case "sync":
            return sync_transport
        case "async_asgi":
            return asgi_transport(async_asgi_transport)
        case "sync_wsgi":
            return sync_wsgi_transport
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)


@pytest.fixture
def client(
    async_transport: HTTPTransport,
    sync_transport: SyncHTTPTransport,
    async_asgi_transport: Transport | None,
    sync_wsgi_transport: SyncTransport,
    client_type: str,
) -> Client | SyncClient:
    match client_type:
        case "async":
            return Client(async_transport)
        case "sync":
            return SyncClient(sync_transport)
        case "async_asgi":
            return Client(asgi_transport(async_asgi_transport))
        case "sync_wsgi":
            return SyncClient(sync_wsgi_transport)
        case _:
            msg = "Invalid client type"
            raise ValueError(msg)

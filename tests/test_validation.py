from __future__ import annotations

import pytest

from pyqwest import HTTPTransport, SyncHTTPTransport


def test_invalid_client_cert(client_type: str) -> None:
    with pytest.raises(ValueError, match="Failed to parse tls_cert"):
        if client_type == "sync":
            SyncHTTPTransport(tls_key=b"invalid", tls_cert=b"invalid")
        else:
            HTTPTransport(tls_key=b"invalid", tls_cert=b"invalid")


def test_only_client_cert(client_type: str) -> None:
    with pytest.raises(ValueError, match="Both tls_key and tls_cert must be provided"):
        if client_type == "sync":
            SyncHTTPTransport(tls_cert=b"unused")
        else:
            HTTPTransport(tls_cert=b"unused")


def test_only_client_key(client_type: str) -> None:
    with pytest.raises(ValueError, match="Both tls_key and tls_cert must be provided"):
        if client_type == "sync":
            SyncHTTPTransport(tls_key=b"unused")
        else:
            HTTPTransport(tls_key=b"unused")


@pytest.mark.asyncio
async def test_transport_invalid_option() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        HTTPTransport(timeout=-1)

    with pytest.raises(ValueError, match="non-negative"):
        HTTPTransport(connect_timeout=float("inf"))

    with pytest.raises(ValueError, match="non-negative"):
        HTTPTransport(read_timeout=-5)

    with pytest.raises(ValueError, match="non-negative"):
        HTTPTransport(pool_idle_timeout=float("nan"))

    with pytest.raises(ValueError, match="non-negative"):
        HTTPTransport(tcp_keepalive_interval=-10)


def test_sync_transport_invalid_option() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SyncHTTPTransport(timeout=-1)

    with pytest.raises(ValueError, match="non-negative"):
        SyncHTTPTransport(connect_timeout=float("inf"))

    with pytest.raises(ValueError, match="non-negative"):
        SyncHTTPTransport(read_timeout=-5)

    with pytest.raises(ValueError, match="non-negative"):
        SyncHTTPTransport(pool_idle_timeout=float("nan"))

    with pytest.raises(ValueError, match="non-negative"):
        SyncHTTPTransport(tcp_keepalive_interval=-10)

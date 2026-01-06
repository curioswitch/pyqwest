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

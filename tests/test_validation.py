from __future__ import annotations

import pytest

from pyqwest import HTTPTransport, SyncHTTPTransport


def test_invalid_client_cert(client_type: str) -> None:
    with pytest.raises(ValueError, match="Failed to parse tls_cert"):
        if client_type == "sync":
            SyncHTTPTransport(tls_key=b"invalid", tls_cert=b"invalid")
        else:
            HTTPTransport(tls_key=b"invalid", tls_cert=b"invalid")


@pytest.mark.parametrize(
    "ca_cert",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"not a pem", id="no-pem-block"),
        pytest.param(b"\x30\x82\x01\x0a\x02\x82\x01\x01", id="der"),
        pytest.param(
            b"-----BEGIN PRIVATE KEY-----\nZm9v\n-----END PRIVATE KEY-----\n", id="key"
        ),
    ],
)
@pytest.mark.parametrize("include_system_certs", [False, True])
def test_ca_cert_without_certificates(
    ca_cert: bytes, include_system_certs: bool, client_type: str
) -> None:
    with pytest.raises(ValueError, match="did not contain any PEM certificates"):
        if client_type == "sync":
            SyncHTTPTransport(
                tls_ca_cert=ca_cert, tls_include_system_certs=include_system_certs
            )
        else:
            HTTPTransport(
                tls_ca_cert=ca_cert, tls_include_system_certs=include_system_certs
            )


def test_unparsable_ca_cert(client_type: str) -> None:
    ca_cert = b"-----BEGIN CERTIFICATE-----\n!!!!\n-----END CERTIFICATE-----\n"
    with pytest.raises(ValueError, match="Failed to parse CA certificate"):
        if client_type == "sync":
            SyncHTTPTransport(tls_ca_cert=ca_cert)
        else:
            HTTPTransport(tls_ca_cert=ca_cert)


def test_invalid_ca_cert(client_type: str) -> None:
    # Valid PEM framing and base64, but the body is not a certificate.
    ca_cert = b"-----BEGIN CERTIFICATE-----\nZm9v\n-----END CERTIFICATE-----\n"
    with pytest.raises(RuntimeError, match="invalid peer certificate"):
        if client_type == "sync":
            SyncHTTPTransport(tls_ca_cert=ca_cert)
        else:
            HTTPTransport(tls_ca_cert=ca_cert)


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

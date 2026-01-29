from __future__ import annotations

import asyncio
import socket
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import pytest
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind, get_current_span

from pyqwest import (
    Client,
    HTTPVersion,
    ReadError,
    StreamError,
    SyncClient,
    SyncResponse,
)

if TYPE_CHECKING:
    from opentelemetry.test.test_base import InMemorySpanExporter

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True),
    pytest.mark.parametrize("client_type", ["async", "sync"]),
]


def version_str(http_version: HTTPVersion) -> str:
    match http_version:
        case HTTPVersion.HTTP2:
            return "2"
        case HTTPVersion.HTTP3:
            return "3"
        case _:
            return "1.1"


@pytest.mark.asyncio
async def test_basic(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion,
    spans_exporter: InMemorySpanExporter,
    server_port: int,
) -> None:
    url = f"{url}/echo?animal=bear"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
            client.post, url, headers=headers, content=req_content
        )
    else:
        resp = await client.post(url, headers=headers, content=req_content)
    assert resp.status == 200

    server_ctx = extract({"traceparent": resp.headers["x-echo-traceparent"]})
    server_span_ctx = get_current_span(server_ctx).get_span_context()

    spans = spans_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    span_ctx = span.get_span_context()
    assert span_ctx is not None
    assert span.name == "POST"
    assert span.kind == SpanKind.CLIENT
    assert span_ctx.trace_id == server_span_ctx.trace_id
    assert span_ctx.span_id == server_span_ctx.span_id
    assert span.parent is None
    assert span.attributes == {
        "http.request.method": "POST",
        "url.full": url,
        "server.address": "localhost",
        "server.port": server_port,
        "network.protocol.name": "http",
        "network.protocol.version": version_str(http_version),
        "http.response.status_code": 200,
    }


@pytest.mark.asyncio
async def test_stream(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion,
    spans_exporter: InMemorySpanExporter,
    server_port: int,
) -> None:
    url = f"{url}/echo?animal=bear"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"

    async with AsyncExitStack() as cleanup:
        if isinstance(client, SyncClient):

            def run() -> SyncResponse:
                return cleanup.enter_context(
                    client.stream("POST", url, headers=headers, content=req_content)
                )

            resp = await asyncio.to_thread(run)
        else:
            resp = await cleanup.enter_async_context(
                client.stream("POST", url, headers=headers, content=req_content)
            )
        assert resp.status == 200

        # Span is done before reading/closing response.
        server_ctx = extract({"traceparent": resp.headers["x-echo-traceparent"]})
        server_span_ctx = get_current_span(server_ctx).get_span_context()

        spans = spans_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        span_ctx = span.get_span_context()
        assert span_ctx is not None
        assert span.name == "POST"
        assert span.kind == SpanKind.CLIENT
        assert span_ctx.trace_id == server_span_ctx.trace_id
        assert span_ctx.span_id == server_span_ctx.span_id
        assert span.parent is None
        assert span.attributes == {
            "http.request.method": "POST",
            "url.full": url,
            "server.address": "localhost",
            "server.port": server_port,
            "network.protocol.name": "http",
            "network.protocol.version": version_str(http_version),
            "http.response.status_code": 200,
        }


@pytest.mark.asyncio
async def test_connection_error(
    client: Client | SyncClient, url: str, spans_exporter: InMemorySpanExporter
) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://localhost:{port}/echo"
    headers = [("content-type", "text/plain")]

    with pytest.raises(ConnectionError):
        if isinstance(client, SyncClient):
            await asyncio.to_thread(client.get, url, headers=headers)
        else:
            await client.get(url, headers=headers)

    spans = spans_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    span_ctx = span.get_span_context()
    assert span_ctx is not None
    assert span.name == "GET"
    assert span.kind == SpanKind.CLIENT
    assert span.parent is None
    assert span.attributes == {
        "http.request.method": "GET",
        "url.full": url,
        "server.address": "localhost",
        "server.port": port,
        "network.protocol.name": "http",
        "error.type": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_response_error(
    client: Client | SyncClient,
    url: str,
    spans_exporter: InMemorySpanExporter,
    http_version: HTTPVersion,
    server_port: int,
) -> None:
    url = f"{url}/echo"
    headers = [("content-type", "text/plain"), ("x-error-response", "1")]

    with pytest.raises((ReadError, StreamError)) as exc_info:
        if isinstance(client, SyncClient):
            await asyncio.to_thread(client.get, url, headers=headers)
        else:
            await client.get(url, headers=headers)

    spans = spans_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    span_ctx = span.get_span_context()
    assert span_ctx is not None
    assert span.name == "GET"
    assert span.kind == SpanKind.CLIENT
    assert span.parent is None
    assert span.attributes == {
        "http.request.method": "GET",
        "url.full": url,
        "server.address": "localhost",
        "server.port": server_port,
        "network.protocol.name": "http",
        "network.protocol.version": version_str(http_version),
        "http.response.status_code": 200,
        "error.type": exc_info.type.__qualname__,
    }

from __future__ import annotations

import asyncio
import socket
from contextlib import AsyncExitStack, ExitStack
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from opentelemetry import context
from opentelemetry.baggage import set_baggage
from opentelemetry.propagate import extract
from opentelemetry.test.test_base import TestBase
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    TraceFlags,
    TraceState,
    get_current_span,
    get_tracer,
    set_span_in_context,
)

from pyqwest import (
    Client,
    HTTPTransport,
    HTTPVersion,
    ReadError,
    StreamError,
    SyncClient,
    SyncHTTPTransport,
    SyncResponse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from opentelemetry.sdk.metrics._internal.point import Histogram, Metric, Sum

    from .conftest import Certs

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


# Make sure there is a separate meterprovider per transport or its not practical
# to differentiate the data points from each.
@pytest.fixture
def otel_test_base() -> Iterator[TestBase]:
    test_base = TestBase()
    test_base.setUp()
    try:
        yield test_base
    finally:
        test_base.tearDown()


@pytest_asyncio.fixture
async def async_transport(
    certs: Certs, http_version: HTTPVersion | None, otel_test_base: TestBase
) -> AsyncIterator[HTTPTransport]:
    async with HTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        meter_provider=otel_test_base.meter_provider,
        tracer_provider=otel_test_base.tracer_provider,
    ) as transport:
        yield transport


@pytest.fixture
def sync_transport(
    certs: Certs, http_version: HTTPVersion | None, otel_test_base: TestBase
) -> Iterator[SyncHTTPTransport]:
    with SyncHTTPTransport(
        tls_ca_cert=certs.ca,
        http_version=http_version,
        meter_provider=otel_test_base.meter_provider,
        tracer_provider=otel_test_base.tracer_provider,
    ) as transport:
        yield transport


@pytest.fixture
def async_client(async_transport: HTTPTransport) -> Client:
    return Client(async_transport)


@pytest.fixture
def sync_client(sync_transport: SyncHTTPTransport) -> SyncClient:
    return SyncClient(sync_transport)


def get_http_metrics(otel_test_base: TestBase) -> list[Metric]:
    metrics = cast("list[Metric]", otel_test_base.get_sorted_metrics())
    return [metric for metric in metrics if metric.name.startswith("http.client.")]


def get_runtime_metrics(otel_test_base: TestBase) -> list[Metric]:
    metrics = cast("list[Metric]", otel_test_base.get_sorted_metrics())
    return [
        metric for metric in metrics if metric.name.startswith("rust.async_runtime.")
    ]


@pytest.mark.asyncio
async def test_basic(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion,
    otel_test_base: TestBase,
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

    spans = otel_test_base.memory_exporter.get_finished_spans()
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

    metrics = get_http_metrics(otel_test_base)
    assert len(metrics) == 2
    active_requests_metric = metrics[0]
    assert active_requests_metric.name == "http.client.active_requests"
    assert active_requests_metric.unit == "{request}"
    assert active_requests_metric.description == "Number of active HTTP requests."
    active_requests_data = cast("Sum", active_requests_metric.data)
    assert len(active_requests_data.data_points) == 1
    assert active_requests_data.data_points[0].value == 0
    assert active_requests_data.data_points[0].attributes == {
        "http.request.method": "POST",
        "server.address": "localhost",
        "server.port": server_port,
    }
    assert len(active_requests_data.data_points[0].exemplars) == 1
    assert (
        active_requests_data.data_points[0].exemplars[0].trace_id == span_ctx.trace_id
    )
    assert active_requests_data.data_points[0].exemplars[0].span_id == span_ctx.span_id

    request_duration_metric = metrics[1]
    assert request_duration_metric.name == "http.client.request.duration"
    assert request_duration_metric.unit == "s"
    assert request_duration_metric.description == "Duration of HTTP client requests."
    request_duration_data = cast("Histogram", request_duration_metric.data)
    assert len(request_duration_data.data_points) == 1
    assert request_duration_data.data_points[0].count == 1
    assert request_duration_data.data_points[0].attributes == {
        "http.request.method": "POST",
        "http.response.status_code": 200,
        "network.protocol.name": "http",
        "network.protocol.version": version_str(http_version),
        "server.address": "localhost",
        "server.port": server_port,
    }
    assert len(request_duration_data.data_points[0].exemplars) == 1
    assert (
        request_duration_data.data_points[0].exemplars[0].trace_id == span_ctx.trace_id
    )
    assert request_duration_data.data_points[0].exemplars[0].span_id == span_ctx.span_id


@pytest.mark.asyncio
async def test_stream(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion,
    otel_test_base: TestBase,
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

        spans = otel_test_base.memory_exporter.get_finished_spans()
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

        metrics = get_http_metrics(otel_test_base)
        assert len(metrics) == 2
        active_requests_metric = metrics[0]
        assert active_requests_metric.name == "http.client.active_requests"
        assert active_requests_metric.unit == "{request}"
        assert active_requests_metric.description == "Number of active HTTP requests."
        active_requests_data = cast("Sum", active_requests_metric.data)
        assert len(active_requests_data.data_points) == 1
        assert active_requests_data.data_points[0].value == 0
        assert active_requests_data.data_points[0].attributes == {
            "http.request.method": "POST",
            "server.address": "localhost",
            "server.port": server_port,
        }

        request_duration_metric = metrics[1]
        assert request_duration_metric.name == "http.client.request.duration"
        assert request_duration_metric.unit == "s"
        assert (
            request_duration_metric.description == "Duration of HTTP client requests."
        )
        request_duration_data = cast("Histogram", request_duration_metric.data)
        assert len(request_duration_data.data_points) == 1
        assert request_duration_data.data_points[0].count == 1
        assert request_duration_data.data_points[0].attributes == {
            "http.request.method": "POST",
            "http.response.status_code": 200,
            "network.protocol.name": "http",
            "network.protocol.version": version_str(http_version),
            "server.address": "localhost",
            "server.port": server_port,
        }


@pytest.mark.asyncio
async def test_connection_error(
    client: Client | SyncClient, url: str, otel_test_base: TestBase
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

    spans = otel_test_base.memory_exporter.get_finished_spans()
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

    metrics = get_http_metrics(otel_test_base)
    assert len(metrics) == 2
    active_requests_metric = metrics[0]
    assert active_requests_metric.name == "http.client.active_requests"
    assert active_requests_metric.unit == "{request}"
    assert active_requests_metric.description == "Number of active HTTP requests."
    active_requests_data = cast("Sum", active_requests_metric.data)
    assert len(active_requests_data.data_points) == 1
    assert active_requests_data.data_points[0].value == 0
    assert active_requests_data.data_points[0].attributes == {
        "http.request.method": "GET",
        "server.address": "localhost",
        "server.port": port,
    }

    request_duration_metric = metrics[1]
    assert request_duration_metric.name == "http.client.request.duration"
    assert request_duration_metric.unit == "s"
    assert request_duration_metric.description == "Duration of HTTP client requests."
    request_duration_data = cast("Histogram", request_duration_metric.data)
    assert len(request_duration_data.data_points) == 1
    assert request_duration_data.data_points[0].count == 1
    assert request_duration_data.data_points[0].attributes == {
        "http.request.method": "GET",
        "server.address": "localhost",
        "server.port": port,
        "network.protocol.name": "http",
        "error.type": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_response_error(
    client: Client | SyncClient,
    url: str,
    otel_test_base: TestBase,
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

    spans = otel_test_base.memory_exporter.get_finished_spans()
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

    metrics = get_http_metrics(otel_test_base)
    assert len(metrics) == 2
    active_requests_metric = metrics[0]
    assert active_requests_metric.name == "http.client.active_requests"
    assert active_requests_metric.unit == "{request}"
    assert active_requests_metric.description == "Number of active HTTP requests."
    active_requests_data = cast("Sum", active_requests_metric.data)
    assert len(active_requests_data.data_points) == 1
    assert active_requests_data.data_points[0].value == 0
    assert active_requests_data.data_points[0].attributes == {
        "http.request.method": "GET",
        "server.address": "localhost",
        "server.port": server_port,
    }

    request_duration_metric = metrics[1]
    assert request_duration_metric.name == "http.client.request.duration"
    assert request_duration_metric.unit == "s"
    assert request_duration_metric.description == "Duration of HTTP client requests."
    request_duration_data = cast("Histogram", request_duration_metric.data)
    assert len(request_duration_data.data_points) == 1
    assert request_duration_data.data_points[0].count == 1
    assert request_duration_data.data_points[0].attributes == {
        "http.request.method": "GET",
        "server.address": "localhost",
        "server.port": server_port,
        "network.protocol.name": "http",
        "network.protocol.version": version_str(http_version),
        "http.response.status_code": 200,
        "error.type": exc_info.type.__qualname__,
    }


def ctx_with_tracestate(trace_state: TraceState) -> context.Context:
    return set_span_in_context(
        NonRecordingSpan(
            SpanContext(
                trace_id=0x4BF92F3577B34DA6A3CE929D0E0E4736,
                span_id=0x00F067AA0BA902B7,
                is_remote=True,
                trace_flags=TraceFlags(TraceFlags.SAMPLED),
                trace_state=trace_state,
            )
        )
    )


@pytest.mark.asyncio
async def test_parent(
    client: Client | SyncClient,
    url: str,
    http_version: HTTPVersion,
    otel_test_base: TestBase,
    server_port: int,
) -> None:
    tracer = get_tracer("test")

    ctx = ctx_with_tracestate(TraceState([("food", "pizza")]))
    ctx = set_baggage("animal", "bear", ctx)
    reset_ctx = context.attach(ctx)

    cleanup = ExitStack()
    cleanup.callback(context.detach, reset_ctx)

    with cleanup, tracer.start_as_current_span("parent") as parent:
        ctx = context.get_current()
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

        tracestate_header = resp.headers.get("x-echo-tracestate", "")
        assert "food=pizza" in tracestate_header

        baggage_header = resp.headers.get("x-echo-baggage", "")
        assert "animal=bear" in baggage_header

        spans = otel_test_base.memory_exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        span_ctx = span.get_span_context()
        assert span_ctx is not None
        assert span.name == "POST"
        assert span.kind == SpanKind.CLIENT
        assert span_ctx.trace_id == server_span_ctx.trace_id
        assert span_ctx.span_id == server_span_ctx.span_id
        assert span_ctx.trace_id == parent.get_span_context().trace_id
        assert span.parent == parent.get_span_context()
        assert span.attributes == {
            "http.request.method": "POST",
            "url.full": url,
            "server.address": "localhost",
            "server.port": server_port,
            "network.protocol.name": "http",
            "network.protocol.version": version_str(http_version),
            "http.response.status_code": 200,
        }

    # Would be a disaster for other tests if we leaked, confirm it since it's easy.
    assert context.get_current() == {}


@pytest.mark.asyncio
async def test_disable_otel(
    url: str,
    otel_test_base: TestBase,
    client_type: str,
    certs: Certs,
    http_version: HTTPVersion,
) -> None:
    match client_type:
        case "async":
            async with HTTPTransport(
                tls_ca_cert=certs.ca,
                http_version=http_version,
                enable_otel=False,
                meter_provider=otel_test_base.meter_provider,
                tracer_provider=otel_test_base.tracer_provider,
            ) as transport:
                client = Client(transport)
                url = f"{url}/echo?animal=bear"
                headers = [("content-type", "text/plain")]
                req_content = b"Hello, World!"
                resp = await client.post(url, headers=headers, content=req_content)
        case "sync":
            with SyncHTTPTransport(
                tls_ca_cert=certs.ca,
                http_version=http_version,
                enable_otel=False,
                meter_provider=otel_test_base.meter_provider,
                tracer_provider=otel_test_base.tracer_provider,
            ) as transport:
                client = SyncClient(transport)
                url = f"{url}/echo?animal=bear"
                headers = [("content-type", "text/plain")]
                req_content = b"Hello, World!"
                resp = client.post(url, headers=headers, content=req_content)
    assert resp.status == 200

    spans = otel_test_base.memory_exporter.get_finished_spans()
    assert len(spans) == 0

    metrics = get_http_metrics(otel_test_base)
    assert len(metrics) == 0

from __future__ import annotations

import contextlib
import contextvars
import inspect
from typing import TYPE_CHECKING, cast

import anyio
import pytest
import sniffio
from anyio import to_thread
from opentelemetry.test.test_base import TestBase

from pyqwest import (
    Client,
    HTTPTransport,
    Request,
    SyncClient,
    SyncHTTPTransport,
    SyncRequest,
    TooManyRedirects,
    get_default_sync_transport,
    get_default_transport,
)

from ._util import SyncRequestBody, run_child

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from opentelemetry.sdk.metrics._internal.point import Histogram, Metric, Sum

pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


@pytest.mark.anyio
async def test_default_transport(url: str) -> None:
    transport = get_default_transport()
    url = f"{url}/echo"
    res = await transport.execute(Request("GET", url))
    assert res.status == 200


@pytest.mark.anyio
async def test_default_sync_transport(url: str) -> None:
    transport = get_default_sync_transport()
    url = f"{url}/echo"
    res = await to_thread.run_sync(transport.execute_sync, SyncRequest("GET", url))
    assert res.status == 200


@pytest.mark.anyio
async def test_default_client(url: str) -> None:
    client = Client()
    url = f"{url}/echo"
    res = await client.get(url)
    assert res.status == 200
    assert res.content == b""


@pytest.mark.anyio
async def test_default_sync_client(url: str) -> None:
    client = SyncClient()
    url = f"{url}/echo"
    res = await to_thread.run_sync(client.get, url)
    assert res.status == 200
    assert res.content == b""


@pytest.mark.anyio
async def test_status_codes(url: str, subtests: pytest.Subtests) -> None:
    client = Client()
    url = f"{url}/echo"
    for i in range(200, 599):
        with subtests.test(f"status={i}"):
            res = await client.get(url, {"x-response-status": str(i)})
            assert res.status == i


@pytest.mark.anyio
async def test_status_codes_sync(url: str, subtests: pytest.Subtests) -> None:
    client = SyncClient()
    url = f"{url}/echo"
    for i in range(200, 599):
        with subtests.test(f"status={i}"):
            res = await to_thread.run_sync(
                client.get, url, {"x-response-status": str(i)}
            )
            assert res.status == i


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.anyio
async def test_transport_options(url: str) -> None:
    async with HTTPTransport(
        timeout=0.001,
        connect_timeout=10,
        read_timeout=20,
        pool_idle_timeout=30,
        pool_max_idle_per_host=5,
        tcp_keepalive_interval=100,
        enable_gzip=True,
        enable_brotli=True,
        enable_zstd=True,
        use_system_dns=True,
    ) as transport:

        async def request_content() -> AsyncIterator[bytes]:
            await anyio.sleep(1)
            yield b"hello"

        url = f"{url}/echo"
        with pytest.raises(TimeoutError):
            async with await transport.execute(
                Request("POST", url, content=request_content())
            ) as res:
                async for _ in res.content:
                    pass

    await transport.aclose()  # double close allowed

    with pytest.raises(RuntimeError, match="already closed transport"):
        await transport.execute(Request("GET", url))

    with pytest.raises(RuntimeError, match="already closed transport"):
        await Client(transport).get(url)


# Most options are performance related and can't really be
# tested but it's worth adding coverage for them anyways.
@pytest.mark.anyio
async def test_sync_transport_options(url: str) -> None:
    with SyncHTTPTransport(
        timeout=0.001,
        connect_timeout=10,
        read_timeout=20,
        pool_idle_timeout=30,
        pool_max_idle_per_host=5,
        tcp_keepalive_interval=100,
        enable_gzip=True,
        enable_brotli=True,
        enable_zstd=True,
        use_system_dns=True,
    ) as transport:
        request_content = SyncRequestBody()

        url = f"{url}/echo"
        with (
            pytest.raises(TimeoutError),
            transport.execute_sync(
                SyncRequest("POST", url, content=request_content)
            ) as res,
        ):
            b"".join(res.content)

    transport.close()  # double close allowed
    with pytest.raises(RuntimeError, match="already closed transport"):
        transport.execute_sync(SyncRequest("GET", url))

    with pytest.raises(RuntimeError, match="already closed transport"):
        SyncClient(transport).get(url)


@pytest.mark.anyio
async def test_cookie_store(url: str) -> None:
    async with HTTPTransport(enable_cookie_store=True) as transport:
        client = Client(transport)
        await client.get(f"{url}/set-cookie")
        res = await client.get(f"{url}/get-cookie")
        assert res.content == b"testcookie=hello"


@pytest.mark.anyio
async def test_cookie_store_disabled(url: str) -> None:
    async with HTTPTransport() as transport:
        client = Client(transport)
        await client.get(f"{url}/set-cookie")
        res = await client.get(f"{url}/get-cookie")
        assert res.content == b""


@pytest.mark.anyio
async def test_cookie_store_sync(url: str) -> None:
    with SyncHTTPTransport(enable_cookie_store=True) as transport:
        client = SyncClient(transport)
        await to_thread.run_sync(client.get, f"{url}/set-cookie")
        res = await to_thread.run_sync(client.get, f"{url}/get-cookie")
        assert res.content == b"testcookie=hello"


@pytest.mark.anyio
async def test_cookie_store_sync_disabled(url: str) -> None:
    with SyncHTTPTransport() as transport:
        client = SyncClient(transport)
        await to_thread.run_sync(client.get, f"{url}/set-cookie")
        res = await to_thread.run_sync(client.get, f"{url}/get-cookie")
        assert res.content == b""


@pytest.mark.anyio
async def test_redirects_disabled(url: str) -> None:
    async with HTTPTransport(follow_redirects=False) as transport:
        res = await Client(transport).get(f"{url}/redirect")
        assert res.status == 302
        assert res.headers["location"] == "/echo"


@pytest.mark.anyio
async def test_follow_redirects(url: str) -> None:
    async with HTTPTransport() as transport:
        res = await Client(transport).get(f"{url}/redirect?n=3")
        assert res.status == 200
        assert res.headers["x-echo-method"] == "GET"


@pytest.mark.anyio
async def test_follow_redirects_too_many(url: str) -> None:
    async with HTTPTransport(max_redirects=2) as transport:
        with pytest.raises(TooManyRedirects):
            await Client(transport).get(f"{url}/redirect?n=5")


@pytest.mark.anyio
async def test_redirects_disabled_sync(url: str) -> None:
    with SyncHTTPTransport(follow_redirects=False) as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, f"{url}/redirect")
        assert res.status == 302
        assert res.headers["location"] == "/echo"


@pytest.mark.anyio
async def test_follow_redirects_sync(url: str) -> None:
    with SyncHTTPTransport() as transport:
        res = await to_thread.run_sync(SyncClient(transport).get, f"{url}/redirect?n=3")
        assert res.status == 200
        assert res.headers["x-echo-method"] == "GET"


@pytest.mark.anyio
async def test_follow_redirects_too_many_sync(url: str) -> None:
    with (
        SyncHTTPTransport(max_redirects=2) as transport,
        pytest.raises(TooManyRedirects),
    ):
        await to_thread.run_sync(SyncClient(transport).get, f"{url}/redirect?n=5")


@pytest.mark.anyio
async def test_request_body_task_cancelled_on_dropped_response(url: str) -> None:
    body_closed = anyio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            yield b"hello"
            await anyio.Event().wait()
        finally:
            body_closed.set()

    async with HTTPTransport() as transport:
        res = await transport.execute(Request("POST", f"{url}/echo", content=content()))
        assert res.status == 200
        # Dropping the response without closing it must still cancel the
        # request body task, or it would hang on the generator forever.
        del res
        with anyio.fail_after(5):
            await body_closed.wait()


@pytest.mark.anyio
async def test_request_body_task_cancelled_on_cancelled_execute(url: str) -> None:
    body_started = anyio.Event()
    body_closed = anyio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            body_started.set()
            await anyio.Event().wait()
            yield b""
        finally:
            body_closed.set()

    async with HTTPTransport() as transport:

        async def execute() -> None:
            await transport.execute(
                Request("POST", f"{url}/read_all", content=content())
            )

        async with anyio.create_task_group() as tg:
            tg.start_soon(execute)
            await body_started.wait()
            tg.cancel_scope.cancel()
        with anyio.fail_after(5):
            await body_closed.wait()


@pytest.mark.anyio
async def test_cancelled_request_leaves_transport_usable(url: str) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"
        await anyio.sleep_forever()

    async with HTTPTransport() as transport:
        with anyio.move_on_after(0.2) as scope:
            await transport.execute(
                Request("POST", f"{url}/read_all", content=content())
            )
        assert scope.cancelled_caught
        res = await transport.execute(Request("GET", f"{url}/echo"))
        assert res.status == 200


@pytest.mark.anyio
async def test_cancel_while_reading_content(url: str) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"
        await anyio.sleep_forever()

    async with HTTPTransport() as transport:
        res = await transport.execute(Request("POST", f"{url}/echo", content=content()))
        with anyio.move_on_after(0.2) as scope:
            async for _ in res.content:
                pass
        assert scope.cancelled_caught
        await res.content.aclose()  # ty: ignore[unresolved-attribute]
        res = await transport.execute(Request("GET", f"{url}/echo"))
        assert res.status == 200


@pytest.mark.anyio
async def test_aclose_partially_read_body(url: str) -> None:
    async with HTTPTransport() as transport:
        res = await transport.execute(
            Request("POST", f"{url}/echo", content=b"x" * 1_000_000)
        )
        first = await anext(res.content)
        assert len(first) > 0
        await res.content.aclose()  # ty: ignore[unresolved-attribute]
        assert await anext(res.content, None) is None


@pytest.mark.anyio
async def test_request_body_sees_caller_context(url: str) -> None:
    request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id")
    seen: list[str] = []

    async def content() -> AsyncIterator[bytes]:
        seen.append(request_id.get("unset"))
        yield b"hello"

    request_id.set("abc")
    async with HTTPTransport() as transport:
        res = await transport.execute(
            Request("POST", f"{url}/read_all", content=content())
        )
        assert res.status == 200
    assert seen == ["abc"]


@pytest.fixture
def request_metrics() -> Iterator[TestBase]:
    """A meter provider for one test, so a count read at the end is this test's alone."""
    test_base = TestBase()
    test_base.setUp()
    try:
        yield test_base
    finally:
        test_base.tearDown()


def active_requests(test_base: TestBase) -> int | float:
    metrics = cast("list[Metric]", test_base.get_sorted_metrics())
    active = next(m for m in metrics if m.name == "http.client.active_requests")
    return cast("Sum", active.data).data_points[0].value


@pytest.mark.anyio
async def test_unawaited_request_completes(url: str, request_metrics: TestBase) -> None:
    # The request runs once `execute()` is called, whether or not its awaitable
    # is awaited, and its done callback ends the operation. Laziness is the
    # `Client` wrapper's job, not the transport's.
    async with HTTPTransport(
        meter_provider=request_metrics.meter_provider
    ) as transport:
        awaitable = transport.execute(Request("GET", f"{url}/echo"))
        if inspect.iscoroutine(awaitable):  # trio; asyncio returns a running Future
            awaitable.close()
        del awaitable
        # Polls a metric, which nothing in the test can signal.
        with anyio.fail_after(5):
            while active_requests(request_metrics) != 0:  # noqa: ASYNC110
                await anyio.sleep(0.01)
    metrics = cast("list[Metric]", request_metrics.get_sorted_metrics())
    duration = next(m for m in metrics if m.name == "http.client.request.duration")
    (point,) = cast("Histogram", duration.data).data_points
    # Completed rather than cancelled.
    assert point.attributes is not None
    assert point.attributes["http.response.status_code"] == 200
    assert "error.type" not in point.attributes


def test_asyncio_without_sniffio(url: str) -> None:
    # The transport imports sniffio once per process, so blocking that import
    # needs a fresh interpreter.
    run_child("_no_sniffio_child.py", f"{url}/echo", prints="200")


@contextlib.contextmanager
def sniffio_reports(name: str) -> Iterator[None]:
    previous = sniffio.thread_local.name
    sniffio.thread_local.name = name
    try:
        yield
    finally:
        sniffio.thread_local.name = previous


@pytest.mark.anyio
async def test_response_content_reuses_detected_library(url: str) -> None:
    res = await get_default_transport().execute(Request("GET", f"{url}/echo"))
    assert res.status == 200
    # execute() detected the running library; reading the body reuses that
    # answer rather than asking sniffio again, so a different answer now has
    # no effect.
    with sniffio_reports("curio"):
        async for _ in res.content:
            pass


@pytest.mark.anyio
async def test_unsupported_async_library(url: str) -> None:
    transport = get_default_transport()
    with (
        sniffio_reports("curio"),
        pytest.raises(
            RuntimeError, match="pyqwest supports asyncio and trio, not 'curio'"
        ),
    ):
        await transport.execute(Request("GET", f"{url}/echo"))

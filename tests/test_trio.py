from __future__ import annotations

import contextvars
import inspect
from typing import TYPE_CHECKING, cast

import pytest
import trio
from opentelemetry.test.test_base import TestBase

from pyqwest import Client, HTTPTransport, Multipart, Part, Request, WriteError

from ._util import run_trio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from opentelemetry.sdk.metrics._internal.point import Histogram, Metric, Sum

    from pyqwest import HTTPVersion


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


# A meter provider per test, so a count read at the end is this test's alone.
@pytest.fixture
def otel_test_base() -> Iterator[TestBase]:
    test_base = TestBase()
    test_base.setUp()
    try:
        yield test_base
    finally:
        test_base.tearDown()


def test_execute(url: str, http_version: HTTPVersion | None) -> None:
    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(Request("GET", f"{url}/echo"))
            assert res.status == 200
            content = b""
            async for chunk in res.content:
                content += chunk
            assert content == b""

    run_trio(main)


def test_streamed_request_body(url: str, http_version: HTTPVersion | None) -> None:
    chunk = b"x" * 65536
    n = 50
    body_closed = False

    async def content() -> AsyncIterator[bytes]:
        nonlocal body_closed
        try:
            for _ in range(n):
                await trio.lowlevel.checkpoint()
                yield chunk
        finally:
            body_closed = True

    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(
                Request("POST", f"{url}/echo", content=content())
            )
            assert res.status == 200
            received = 0
            async for c in res.content:
                received += len(c)
            assert received == n * len(chunk)
        assert body_closed

    run_trio(main)


def test_cancelled_request_leaves_run_healthy(
    url: str, http_version: HTTPVersion | None
) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"
        await trio.sleep_forever()

    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            with trio.move_on_after(0.2) as scope:
                await transport.execute(
                    Request("POST", f"{url}/read_all", content=content())
                )
            assert scope.cancelled_caught
            res = await transport.execute(Request("GET", f"{url}/echo"))
            assert res.status == 200

    run_trio(main)


def test_request_body_task_cancelled_on_dropped_response(
    url: str, http_version: HTTPVersion | None
) -> None:
    body_closed = trio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            yield b"hello"
            await trio.sleep_forever()
        finally:
            body_closed.set()

    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(
                Request("POST", f"{url}/echo", content=content())
            )
            assert res.status == 200
            # Dropping the response without closing it must still cancel the
            # request body task, or it would hang on the generator forever.
            del res
            with trio.fail_after(5):
                await body_closed.wait()

    run_trio(main)


def test_request_body_task_cancelled_on_cancelled_execute(
    url: str, http_version: HTTPVersion | None
) -> None:
    body_started = trio.Event()
    body_closed = trio.Event()

    async def content() -> AsyncIterator[bytes]:
        try:
            body_started.set()
            await trio.sleep_forever()
            yield b""
        finally:
            body_closed.set()

    async def main() -> None:
        async with (
            HTTPTransport(http_version=http_version) as transport,
            trio.open_nursery() as nursery,
        ):

            async def execute() -> None:
                await transport.execute(
                    Request("POST", f"{url}/read_all", content=content())
                )

            nursery.start_soon(execute)
            await body_started.wait()
            nursery.cancel_scope.cancel()
        with trio.fail_after(5):
            await body_closed.wait()

    run_trio(main)


def test_request_body_error_is_reported(
    url: str, http_version: HTTPVersion | None
) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"
        msg = "body failed"
        raise RuntimeError(msg)

    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            # Over HTTP/2 hyper reports a stream reset instead of the body's
            # message, under asyncio too, so only the error type is checked.
            with pytest.raises(WriteError):
                res = await transport.execute(
                    Request("POST", f"{url}/read_all", content=content())
                )
                async for _ in res.content:
                    pass

    run_trio(main)


def test_cancel_while_reading_content(
    url: str, http_version: HTTPVersion | None
) -> None:
    async def content() -> AsyncIterator[bytes]:
        yield b"hello"
        await trio.sleep_forever()

    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(
                Request("POST", f"{url}/echo", content=content())
            )
            with trio.move_on_after(0.2) as scope:
                async for _ in res.content:
                    pass
            assert scope.cancelled_caught
            await res.content.aclose()  # ty: ignore[unresolved-attribute]
            res = await transport.execute(Request("GET", f"{url}/echo"))
            assert res.status == 200

    run_trio(main)


def test_aclose_partially_read_body(url: str, http_version: HTTPVersion | None) -> None:
    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(
                Request("POST", f"{url}/echo", content=b"x" * 1_000_000)
            )
            first = await anext(res.content)
            assert len(first) > 0
            await res.content.aclose()  # ty: ignore[unresolved-attribute]
            assert await anext(res.content, None) is None

    run_trio(main)


def test_execute_and_read_full(url: str, http_version: HTTPVersion | None) -> None:
    async def main() -> None:
        async with HTTPTransport(http_version=http_version) as transport:
            res = await Client(transport).post(f"{url}/echo", content=b"payload")
            assert res.status == 200
            assert res.content == b"payload"

    run_trio(main)


def test_default_transport_sends_multipart(url: str) -> None:
    async def stream() -> AsyncIterator[bytes]:
        yield b"stream "
        yield b"chunks"

    async def main() -> None:
        multipart = Multipart(
            [("field", "hello world"), ("file", Part(stream(), filename="s.bin"))]
        )
        res = await Client().post(f"{url}/echo", content=multipart)
        assert res.status == 200
        assert res.headers["x-echo-content-type"].startswith("multipart/form-data")
        assert b"hello world" in res.content
        assert b"stream chunks" in res.content

    run_trio(main)


def test_request_body_sees_caller_context(
    url: str, http_version: HTTPVersion | None
) -> None:
    request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id")
    seen: list[str] = []

    async def content() -> AsyncIterator[bytes]:
        seen.append(request_id.get("unset"))
        yield b"hello"

    async def main() -> None:
        request_id.set("abc")
        async with HTTPTransport(http_version=http_version) as transport:
            res = await transport.execute(
                Request("POST", f"{url}/read_all", content=content())
            )
            assert res.status == 200
        assert seen == ["abc"]

    run_trio(main)


def active_requests(test_base: TestBase) -> int | float:
    metrics = cast("list[Metric]", test_base.get_sorted_metrics())
    active = next(m for m in metrics if m.name == "http.client.active_requests")
    return cast("Sum", active.data).data_points[0].value


def test_unawaited_request_completes(url: str, otel_test_base: TestBase) -> None:
    # As under asyncio, the request runs once `execute()` is called, whether or
    # not its awaitable is awaited, and its done callback ends the operation.
    async def main() -> None:
        async with HTTPTransport(
            meter_provider=otel_test_base.meter_provider
        ) as transport:
            awaitable = transport.execute(Request("GET", f"{url}/echo"))
            assert inspect.iscoroutine(awaitable)
            awaitable.close()
            # Polls a metric, which nothing in the run can signal.
            with trio.fail_after(5):
                while active_requests(otel_test_base) != 0:  # noqa: ASYNC110
                    await trio.sleep(0.01)

    run_trio(main)
    metrics = cast("list[Metric]", otel_test_base.get_sorted_metrics())
    duration = next(m for m in metrics if m.name == "http.client.request.duration")
    (point,) = cast("Histogram", duration.data).data_points
    # Completed rather than cancelled.
    assert point.attributes is not None
    assert point.attributes["http.response.status_code"] == 200
    assert "error.type" not in point.attributes

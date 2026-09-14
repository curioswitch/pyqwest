from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from functools import partial
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import anyio
import pytest
from anyio import to_thread

from pyqwest import (
    Client,
    FullResponse,
    Headers,
    HTTPVersion,
    ReadError,
    RemoteProtocolError,
    SyncClient,
    WriteError,
)

from ._util import SyncRequestBody, hanging_body

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from anyio.streams.memory import MemoryObjectReceiveStream

    from pyqwest import HTTPTransport


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2", "h3", "auto"], indirect=True),
]


def supports_trailers(http_version: HTTPVersion | None, url: str) -> bool:
    # Currently reqwest trailers patch does not apply to HTTP/3.
    return http_version == HTTPVersion.HTTP2 or (
        http_version is None and url.startswith("https://")
    )


async def request_body(
    receive: MemoryObjectReceiveStream[bytes | None],
) -> AsyncIterator[bytes]:
    async with receive:
        async for item in receive:
            if item is None:
                return
            yield item


@pytest.mark.anyio
async def test_basic(
    client: Client | SyncClient, url: str, http_version: HTTPVersion, server_port: int
) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):

        def run():
            with client.stream(
                method, url, headers, req_content, params={"foo": "bar"}
            ) as resp:
                content = b"".join(resp.content)
            return (resp, content)

        resp, content = await to_thread.run_sync(run)
    else:
        async with client.stream(
            method, url, headers, req_content, params={"foo": "bar"}
        ) as resp:
            content = b""
            async for chunk in resp.content:
                content += chunk
    assert resp.status == 200
    assert resp.headers["x-echo-host"] == f"localhost:{server_port}"
    assert resp.headers["x-echo-method"] == "POST"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert content == b"Hello, World!"
    # Didn't send te so should be no trailers
    assert len(resp.trailers) == 0
    if http_version is not None:
        assert resp.http_version == http_version
    else:
        if url.startswith("https://"):
            # Currently it seems HTTP/3 is not added to ALPN and must be explicitly
            # set when creating a Client.
            assert resp.http_version == HTTPVersion.HTTP2
        else:
            assert resp.http_version == HTTPVersion.HTTP1


@pytest.mark.anyio
async def test_iterable_body(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):

        def run():
            with client.stream(method, url, content=[b"Hello, ", b"World!"]) as resp:
                content = b"".join(resp.content)
            return (resp, content)

        resp, content = await to_thread.run_sync(run)
    else:

        async def req_content() -> AsyncIterator[bytes]:
            yield b"Hello, "
            yield b"World!"

        async with client.stream(method, url, content=req_content()) as resp:
            content = b""
            async for chunk in resp.content:
                content += chunk
    assert resp.status == 200
    assert content == b"Hello, World!"


@pytest.mark.anyio
async def test_empty_request(client: Client | SyncClient, url: str) -> None:
    method = "GET"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):

        def run():
            with client.stream(method, url) as resp:
                content = b"".join(resp.content)
            return (resp, content)

        resp, content = await to_thread.run_sync(run)
    else:
        async with client.stream(method, url) as resp:
            content = b""
            async for chunk in resp.content:
                content += chunk
    assert resp.status == 200
    assert content == b""


@pytest.mark.anyio
async def test_bidi(
    async_client: Client, url: str, http_version: HTTPVersion | None
) -> None:
    client = async_client
    send, receive = anyio.create_memory_object_stream[bytes | None](math.inf)

    async with (
        send,
        client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=request_body(receive),
        ) as resp,
    ):
        assert resp.status == 200
        content = resp.content
        await send.send(b"Hello!")
        chunk = await anext(content)
        assert chunk == b"Hello!"
        await send.send(b" World!")
        chunk = await anext(content)
        assert chunk == b" World!"
        await send.send(None)
        chunk = await anext(content, None)
        assert chunk is None
        if supports_trailers(http_version, url):
            assert resp.trailers["x-echo-trailer"] == "last info"
        else:
            assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_bidi_sync(
    sync_client: SyncClient, url: str, http_version: HTTPVersion | None
) -> None:
    client = sync_client

    def run():
        request_body = SyncRequestBody()
        with client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=request_body,
        ) as resp:
            assert resp.status == 200
            content = resp.content
            request_body.put(b"Hello!")
            chunk = next(content)
            assert chunk == b"Hello!"
            request_body.put(b" World!")
            chunk = next(content)
            assert chunk == b" World!"
            request_body.close()
            chunk = next(content, None)
            assert chunk is None
            if supports_trailers(http_version, url):
                assert resp.trailers["x-echo-trailer"] == "last info"
            else:
                assert len(resp.trailers) == 0

    await to_thread.run_sync(run)


@pytest.mark.anyio
async def test_large_body(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = Headers(
        [
            ("content-type", "text/plain"),
            ("x-hello", "rust"),
            ("x-hello", "python"),
            ("te", "trailers"),
        ]
    )
    if isinstance(client, SyncClient):

        def run():
            with client.stream(method, url, headers, [b"Hello!"] * 100) as resp:
                content = b"".join(resp.content)
            return (resp, content)

        resp, content = await to_thread.run_sync(run)
    else:

        async def async_req_content() -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b"Hello!"

        async with client.stream(method, url, headers, async_req_content()) as resp:
            content = b""
            async for chunk in resp.content:
                content += chunk
    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert content == b"Hello!" * 100, len(content)
    if supports_trailers(http_version, url):
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_readall(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/read_all"
    headers = Headers([("content-type", "text/plain")])
    if isinstance(client, SyncClient):

        def run():
            with client.stream(method, url, headers, [b"Hello!"] * 100) as resp:
                content = b"".join(resp.content)
            return (resp, content)

        resp, content = await to_thread.run_sync(run)
    else:

        async def async_req_content() -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b"Hello!"

        async with client.stream(method, url, headers, async_req_content()) as resp:
            content = b""
            async for chunk in resp.content:
                content += chunk
    assert resp.status == 200
    assert content == b"Hello!" * 100, len(content)


@pytest.mark.anyio
async def test_execute(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            partial(client.execute, method, url, headers, req_content, params=params)
        )
    else:
        resp = await client.execute(method, url, headers, req_content, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "POST"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert resp.content == b"Hello, World!"
    assert resp.text() == "Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_execute_json(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content_obj = {"message": "Hello, World!"}
    req_content = json.dumps(req_content_obj).encode("utf-8")
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            client.execute, method, url, headers, req_content
        )
    else:
        resp = await client.execute(method, url, headers, req_content)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "POST"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert resp.content == req_content
    assert resp.json() == req_content_obj
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_get(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(partial(client.get, url, params=params))
    else:
        resp = await client.get(url, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "GET"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_post(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    headers = [("content-type", "text/plain"), ("te", "trailers")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            partial(client.post, url, headers, req_content, params=params)
        )
    else:
        resp = await client.post(url, headers, req_content, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "POST"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    if supports_trailers(http_version, url):
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_delete(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(partial(client.delete, url, params=params))
    else:
        resp = await client.delete(url, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "DELETE"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_head(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(partial(client.head, url, params=params))
    else:
        resp = await client.head(url, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "HEAD"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_options(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(partial(client.options, url, params=params))
    else:
        resp = await client.options(url, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "OPTIONS"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_patch(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            partial(client.patch, url, headers, req_content, params=params)
        )
    else:
        resp = await client.patch(url, headers, req_content, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PATCH"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_put(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    params: dict[str, str | None] = {"foo": "bar"}
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            partial(client.put, url, headers, req_content, params=params)
        )
    else:
        resp = await client.put(url, headers, req_content, params=params)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PUT"
    assert resp.headers["x-echo-query-string"] == "foo=bar"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.anyio
async def test_nihongo(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/日本語 英語?q=テスト&ほげ=fo%26o"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(client.get, url)
    else:
        resp = await client.get(url)
    assert resp.status == 200
    qs = parse_qs(resp.headers["x-echo-query-string"])
    assert qs["q"] == ["テスト"]
    assert qs["ほげ"] == ["fo&o"]


@pytest.mark.anyio
@pytest.mark.parametrize("encoding", ["br", "gzip", "zstd", "identity"])
async def test_content_encoding(
    client: Client | SyncClient, url: str, encoding: str
) -> None:
    url = f"{url}/content-encoding"
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(client.get, url, {"accept-encoding": encoding})
    else:
        resp = await client.get(url, {"accept-encoding": encoding})
    assert resp.status == 200
    assert resp.content == b"Hello World!!!!!"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method", ["POST", "PUT", "PATCH", "EXECUTE_POST", "STREAM_POST"]
)
async def test_json_content(client: Client | SyncClient, url: str, method: str) -> None:
    url = f"{url}/echo"
    content = {"message": "Hello, World!"}
    if isinstance(client, SyncClient):
        match method:
            case "POST":
                resp = await to_thread.run_sync(
                    partial(client.post, url, content=content)
                )
            case "PUT":
                resp = await to_thread.run_sync(
                    partial(client.put, url, content=content)
                )
            case "PATCH":
                resp = await to_thread.run_sync(
                    partial(client.patch, url, content=content)
                )
            case "EXECUTE_POST":
                resp = await to_thread.run_sync(
                    partial(client.execute, "POST", url, content=content)
                )
            case "STREAM_POST":

                def run():
                    with client.stream("POST", url, content=content) as resp:
                        resp_content = b"".join(resp.content)
                    return FullResponse(
                        resp.status, resp.headers, resp_content, resp.trailers
                    )

                resp = await to_thread.run_sync(run)
    else:
        match method:
            case "POST":
                resp = await client.post(url, content=content)
            case "PUT":
                resp = await client.put(url, content=content)
            case "PATCH":
                resp = await client.patch(url, content=content)
            case "EXECUTE_POST":
                resp = await client.execute("POST", url, content=content)
            case "STREAM_POST":
                async with client.stream("POST", url, content=content) as resp:
                    resp_content = b""
                    async for chunk in resp.content:
                        resp_content += chunk
                resp = FullResponse(
                    resp.status, resp.headers, resp_content, resp.trailers
                )
    assert resp.status == 200
    assert resp.headers["content-type"] == "application/json"
    assert resp.content == b'{"message": "Hello, World!"}'
    assert resp.json() == content


@pytest.mark.anyio
async def test_json_content_existing_content_type(
    client: Client | SyncClient, url: str
) -> None:
    url = f"{url}/echo"
    content = {"message": "Hello, World!"}
    if isinstance(client, SyncClient):
        resp = await to_thread.run_sync(
            partial(
                client.post,
                url,
                headers={"content-type": "text/plain"},
                content=content,
            )
        )
    else:
        resp = await client.post(
            url, headers={"content-type": "text/plain"}, content=content
        )
    assert resp.status == 200
    assert resp.headers["content-type"] == "text/plain"
    assert resp.content == b'{"message": "Hello, World!"}'


@pytest.mark.anyio
async def test_close_no_read(async_client: Client, url: str) -> None:
    client = async_client

    request_started = anyio.Event()
    request_cancelled = anyio.Event()
    generator_cancelled = anyio.Event()

    class RequestGenerator:
        def __aiter__(self) -> AsyncIterator[bytes]:
            return self

        async def __anext__(self) -> bytes:
            request_started.set()
            try:
                await anyio.sleep_forever()
            except anyio.get_cancelled_exc_class():
                request_cancelled.set()
                raise
            msg = "sleep_forever returned"
            raise AssertionError(msg)

        async def aclose(self) -> None:
            generator_cancelled.set()

    async with client.stream(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=RequestGenerator(),
    ) as resp:
        assert resp.status == 200
        content = resp.content

    chunk = await anext(content, None)
    assert chunk is None
    await resp.aclose()

    with anyio.fail_after(1):
        if request_started.is_set():
            await request_cancelled.wait()
        await generator_cancelled.wait()


@pytest.mark.anyio
async def test_close_no_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client

    def run():
        request_body = SyncRequestBody()
        with client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=request_body,
        ) as resp:
            assert resp.status == 200
            content = resp.content

        chunk = next(content, None)
        assert chunk is None
        resp.close()
        assert request_body._closed

    await to_thread.run_sync(run)


@pytest.mark.anyio
async def test_close_pending_read(async_client: Client, url: str) -> None:
    client = async_client

    async with (
        anyio.create_task_group() as tg,
        client.stream(
            "POST",
            f"{url}/echo",
            headers={"content-type": "text/plain", "te": "trailers"},
            content=hanging_body(),
        ) as resp,
    ):
        assert resp.status == 200
        content = resp.content

        async def read_content() -> None:
            # Closing the response fails the pending read.
            with pytest.raises(ReadError):
                await anext(content, None)

        tg.start_soon(read_content)

        while not resp._read_pending:  # ty: ignore[unresolved-attribute]  # noqa: ASYNC110
            await anyio.sleep(0.001)

    assert not resp._read_pending  # ty: ignore[unresolved-attribute]


@pytest.mark.anyio
async def test_close_pending_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client
    request_body = SyncRequestBody()

    def run():
        with client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=request_body,
        ) as resp:
            assert resp.status == 200
            content = resp.content

            last_read: memoryview | bytes | bytearray | Exception | None = memoryview(
                b"init"
            )

            def read_content() -> None:
                nonlocal last_read
                try:
                    last_read = next(content, None)
                except Exception as e:
                    last_read = e

            read_thread = threading.Thread(target=read_content)
            read_thread.start()

            while not resp._read_pending:  # ty: ignore[unresolved-attribute]
                time.sleep(0.001)

        read_thread.join()
        assert isinstance(last_read, ReadError)
        while request_body._pending_read:
            time.sleep(0.001)
        assert request_body._closed

    await to_thread.run_sync(run)


@pytest.mark.anyio
async def test_request_content_error(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    # There is a race between whether the error is handled on the request
    # or response side, which can look like a connection error when the server
    # aborts or a response error. We match any.
    with pytest.raises(
        Exception, match=r"Request|connection|reading content"
    ) as exc_info:
        method = "POST"
        url = f"{url}/echo"
        if isinstance(client, SyncClient):

            def req_content_sync() -> Iterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            def run():
                request_content = req_content_sync()
                with client.stream(method, url, content=request_content) as resp:
                    b"".join(resp.content)

            await to_thread.run_sync(run)
        else:

            async def req_content() -> AsyncIterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            async with client.stream(method, url, content=req_content()) as resp:
                content = b""
                async for chunk in resp.content:
                    content += chunk
    if isinstance(exc_info.value, WriteError):
        if http_version is None:
            msg = "Request failed"
        elif http_version != HTTPVersion.HTTP2:
            msg = "Test error"
        else:
            # With HTTP/2, reqwest seems to squash the original error message.
            msg = "stream error sent by user"
        assert msg in str(exc_info.value)
    else:
        assert isinstance(exc_info.value, ReadError)


class BodyInterruptedError(BaseException):
    pass


@pytest.mark.anyio
@pytest.mark.parametrize("error", [BodyInterruptedError, asyncio.CancelledError])
async def test_request_content_interrupted(
    async_transport: HTTPTransport,
    url: str,
    http_version: HTTPVersion | None,
    error: type[BaseException],
) -> None:
    async def req_content() -> AsyncIterator[bytes]:
        yield b"Hello, World!"
        raise error

    # /read_all responds only after reading the whole body, so the failure is
    # on the write side, without the race test_request_content_error allows
    # for. Except on HTTP/3: reqwest finishes the QUIC stream on a body error,
    # so the server gets the truncated body as complete and can respond first.
    with pytest.raises((WriteError, ReadError)) as exc_info:
        await Client(async_transport).post(f"{url}/read_all", content=req_content())
    if http_version != HTTPVersion.HTTP3:
        assert isinstance(exc_info.value, WriteError)
    if http_version is None:
        http_version = (
            HTTPVersion.HTTP2 if url.startswith("https") else HTTPVersion.HTTP1
        )
    if http_version == HTTPVersion.HTTP2:
        # h2 reports its own reset instead of the body's error.
        assert "stream error sent by user: stream no longer needed" in str(
            exc_info.value
        )
    elif isinstance(exc_info.value, WriteError):
        assert str(exc_info.value).endswith("Request body ended before it was complete")


@pytest.mark.anyio
async def test_response_error(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    if http_version in (HTTPVersion.HTTP2, None):
        # https://github.com/envoyproxy/envoy/pull/42269
        pytest.skip("Envoy currently returns successful RST_STREAM")

    status = 0
    # There is a race between whether the error is handled on the request
    # or response side, which looks like a connection error when the server
    # aborts. We match either. Aborting mid-body also leaves the chunked
    # response unterminated, which is a protocol violation.
    with pytest.raises((RemoteProtocolError, ReadError, WriteError)):
        method = "POST"
        url = f"{url}/echo"
        headers = {"x-error-response": "1"}
        request_content = b"Hello"
        if isinstance(client, SyncClient):

            def run():
                nonlocal status
                with client.stream(
                    method, url, headers=headers, content=request_content
                ) as resp:
                    status = resp.status
                    b"".join(resp.content)

            await to_thread.run_sync(run)
        else:
            async with client.stream(
                method, url, headers=headers, content=request_content
            ) as resp:
                status = resp.status
                content = b""
                async for chunk in resp.content:
                    content += chunk
    # Make sure we got response headers before the error
    assert status == 200


@pytest.mark.anyio
async def test_negative_timeout(sync_client: SyncClient, url: str) -> None:
    url = f"{url}/echo"
    with pytest.raises(ValueError, match="Timeout must be non-negative"):
        await to_thread.run_sync(partial(sync_client.get, url, timeout=-5.0))


@pytest.mark.anyio
async def test_infinite_timeout(sync_client: SyncClient, url: str) -> None:
    url = f"{url}/echo"
    with pytest.raises(ValueError, match="Timeout must be non-negative"):
        await to_thread.run_sync(partial(sync_client.get, url, timeout=float("inf")))


@pytest.mark.anyio
async def test_nan_timeout(sync_client: SyncClient, url: str) -> None:
    url = f"{url}/echo"
    with pytest.raises(ValueError, match="Timeout must be non-negative"):
        await to_thread.run_sync(partial(sync_client.get, url, timeout=float("nan")))

from __future__ import annotations

import asyncio
import json
import threading
import time
from queue import Queue
from typing import TYPE_CHECKING

import pytest

from pyqwest import (
    Client,
    FullResponse,
    Headers,
    HTTPVersion,
    ReadError,
    SyncClient,
    WriteError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True),
    pytest.mark.parametrize("http_version", ["h1", "h2", "h3", "auto"], indirect=True),
]


def supports_trailers(http_version: HTTPVersion | None, url: str) -> bool:
    # Currently reqwest trailers patch does not apply to HTTP/3.
    return http_version == HTTPVersion.HTTP2 or (
        http_version is None and url.startswith("https://")
    )


async def request_body(queue: asyncio.Queue) -> AsyncIterator[bytes]:
    while True:
        item: bytes | None = await queue.get()
        if item is None:
            return
        yield item


def sync_request_body(queue: Queue) -> Iterator[bytes]:
    while True:
        item: bytes | None = queue.get()
        if item is None:
            return
        yield item


@pytest.mark.asyncio
async def test_basic(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
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
        resp = await asyncio.to_thread(client.stream, method, url, headers, req_content)
        content = b"".join(resp.content)
    else:
        resp = await client.stream(method, url, headers, req_content)
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
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


@pytest.mark.asyncio
async def test_iterable_body(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
            client.stream, method, url, content=[b"Hello, ", b"World!"]
        )
        content = b"".join(resp.content)
    else:

        async def req_content() -> AsyncIterator[bytes]:
            yield b"Hello, "
            yield b"World!"

        resp = await client.stream(method, url, content=req_content())
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
    assert content == b"Hello, World!"


@pytest.mark.asyncio
async def test_empty_request(client: Client | SyncClient, url: str) -> None:
    method = "GET"
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.stream, method, url)
        content = b"".join(resp.content)
    else:
        resp = await client.stream(method, url)
        content = b""
        async for chunk in resp.content:
            content += chunk
    assert resp.status == 200
    assert content == b""


@pytest.mark.asyncio
async def test_bidi(
    async_client: Client, url: str, http_version: HTTPVersion | None
) -> None:
    client = async_client
    queue = asyncio.Queue()

    async with await client.stream(
        "POST",
        f"{url}/echo",
        headers=Headers({"content-type": "text/plain", "te": "trailers"}),
        content=request_body(queue),
    ) as resp:
        assert resp.status == 200
        content = resp.content
        await queue.put(b"Hello!")
        chunk = await anext(content)
        assert chunk == b"Hello!"
        await queue.put(b" World!")
        chunk = await anext(content)
        assert chunk == b" World!"
        await queue.put(None)
        chunk = await anext(content, None)
        assert chunk is None
        if supports_trailers(http_version, url):
            assert resp.trailers["x-echo-trailer"] == "last info"
        else:
            assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_bidi_sync(
    sync_client: SyncClient, url: str, http_version: HTTPVersion | None
) -> None:
    client = sync_client
    queue = Queue()

    def run():
        with client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        ) as resp:
            assert resp.status == 200
            content = resp.content
            queue.put(b"Hello!")
            chunk = next(content)
            assert chunk == b"Hello!"
            queue.put(b" World!")
            chunk = next(content)
            assert chunk == b" World!"
            queue.put(None)
            chunk = next(content, None)
            assert chunk is None
            if supports_trailers(http_version, url):
                assert resp.trailers["x-echo-trailer"] == "last info"
            else:
                assert len(resp.trailers) == 0

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_large_body(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
        ("te", "trailers"),
    ]
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
            client.stream, method, url, headers, [b"Hello!"] * 100
        )
        content = b"".join(resp.content)
    else:

        async def async_req_content() -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b"Hello!"

        resp = await client.stream(method, url, headers, async_req_content())
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


@pytest.mark.asyncio
async def test_read_full(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
        ("te", "trailers"),
    ]
    if isinstance(client, SyncClient):
        resp: FullResponse
        resp2: FullResponse

        def run():
            nonlocal resp, resp2
            res = client.stream(method, url, headers, [b"Hello!"] * 100)
            resp = res.read_full()
            resp2 = res.read_full()

        await asyncio.to_thread(run)
    else:

        async def async_req_content() -> AsyncIterator[bytes]:
            for _ in range(100):
                yield b"Hello!"

        res = await client.stream(method, url, headers, async_req_content())
        resp = await res.read_full()
        resp2 = await res.read_full()
    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.headers["x-echo-x-hello"] == "rust"
    assert resp.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert resp.content == b"Hello!" * 100
    if supports_trailers(http_version, url):
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert len(resp.trailers) == 0

    # Not recommended usage but check it in case. The content was already
    # consumed and not available.
    assert resp2.status == 200
    assert resp2.headers["x-echo-content-type"] == "text/plain"
    assert resp2.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp2.headers["x-echo-x-hello"] == "rust"
    assert resp2.headers.getall("x-echo-x-hello") == ["rust", "python"]
    assert resp2.content == b""
    if supports_trailers(http_version, url):
        assert resp.trailers["x-echo-trailer"] == "last info"
    else:
        assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_execute(client: Client | SyncClient, url: str) -> None:
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(
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
    assert resp.content == b"Hello, World!"
    assert resp.text() == "Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
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
        resp = await asyncio.to_thread(
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


@pytest.mark.asyncio
async def test_get(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.get, url)
    else:
        resp = await client.get(url)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "GET"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_post(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.post, url, headers, req_content)
    else:
        resp = await client.post(url, headers, req_content)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "POST"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_delete(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.delete, url)
    else:
        resp = await client.delete(url)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "DELETE"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_head(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.head, url)
    else:
        resp = await client.head(url)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "HEAD"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_options(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.options, url)
    else:
        resp = await client.options(url)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "OPTIONS"
    assert resp.content == b""
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_patch(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.patch, url, headers, req_content)
    else:
        resp = await client.patch(url, headers, req_content)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PATCH"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_put(client: Client | SyncClient, url: str) -> None:
    url = f"{url}/echo"
    headers = [("content-type", "text/plain")]
    req_content = b"Hello, World!"
    if isinstance(client, SyncClient):
        resp = await asyncio.to_thread(client.put, url, headers, req_content)
    else:
        resp = await client.put(url, headers, req_content)
    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PUT"
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers.getall("x-echo-content-type") == ["text/plain"]
    assert resp.content == b"Hello, World!"
    assert len(resp.trailers) == 0


@pytest.mark.asyncio
async def test_close_no_read(async_client: Client, url: str) -> None:
    client = async_client
    queue = asyncio.Queue()

    request_cancelled = asyncio.Event()
    generator_cancelled = asyncio.Event()

    class RequestGenerator:
        def __aiter__(self) -> AsyncIterator[bytes]:
            return self

        async def __anext__(self) -> bytes:
            try:
                return await queue.get()
            except asyncio.CancelledError:
                request_cancelled.set()
                raise

        async def aclose(self) -> None:
            generator_cancelled.set()

    resp = await client.stream(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=RequestGenerator(),
    )
    assert resp.status == 200
    content = resp.content

    await resp.close()
    chunk = await anext(content, None)
    assert chunk is None

    await asyncio.wait_for(request_cancelled.wait(), timeout=1.0)
    await asyncio.wait_for(generator_cancelled.wait(), timeout=1.0)


@pytest.mark.asyncio
async def test_close_no_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client
    queue = Queue()

    def run():
        resp = client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        )
        assert resp.status == 200
        content = resp.content

        resp.close()
        chunk = next(content, None)
        assert chunk is None

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_close_pending_read(async_client: Client, url: str) -> None:
    client = async_client
    queue = asyncio.Queue()

    resp = await client.stream(
        "POST",
        f"{url}/echo",
        headers={"content-type": "text/plain", "te": "trailers"},
        content=request_body(queue),
    )
    assert resp.status == 200
    content = resp.content

    async def read_content() -> bytes | None:
        return await anext(content, None)

    read_task = asyncio.create_task(read_content())

    while not resp._read_pending:  # pyright: ignore[reportAttributeAccessIssue]  # noqa: ASYNC110
        await asyncio.sleep(0.001)

    await resp.close()
    chunk = await read_task
    assert chunk is None


@pytest.mark.asyncio
async def test_close_pending_read_sync(sync_client: SyncClient, url: str) -> None:
    client = sync_client
    queue = Queue()

    def run():
        resp = client.stream(
            "POST",
            f"{url}/echo",
            headers=Headers({"content-type": "text/plain", "te": "trailers"}),
            content=sync_request_body(queue),
        )
        assert resp.status == 200
        content = resp.content

        last_read: bytes | None = b"init"

        def read_content() -> bytes | None:
            nonlocal last_read
            last_read = next(content, None)

        read_thread = threading.Thread(target=read_content)
        read_thread.start()

        while not resp._read_pending:  # pyright: ignore[reportAttributeAccessIssue]
            time.sleep(0.001)

        resp.close()
        read_thread.join()
        assert last_read is None

    await asyncio.to_thread(run)


@pytest.mark.asyncio
async def test_request_content_error(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    # There is a race between whether the error is handled on the request
    # or response side, which looks like a connection error when the server
    # aborts. We match either.
    with pytest.raises(Exception, match=r"Request|connection") as exc_info:
        method = "POST"
        url = f"{url}/echo"
        if isinstance(client, SyncClient):

            def req_content_sync() -> Iterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            resp = await asyncio.to_thread(
                client.stream, method, url, content=req_content_sync()
            )
            content = b"".join(resp.content)
        else:

            async def req_content() -> AsyncIterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            resp = await client.stream(method, url, content=req_content())
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


@pytest.mark.asyncio
async def test_response_error(
    client: Client | SyncClient, url: str, http_version: HTTPVersion
) -> None:
    if http_version in (HTTPVersion.HTTP2, None):
        # https://github.com/envoyproxy/envoy/pull/42269
        pytest.skip("Envoy currently returns successful RST_STREAM")

    # There is a race between whether the error is handled on the request
    # or response side, which looks like a connection error when the server
    # aborts. We match either.
    with pytest.raises(ReadError):
        method = "POST"
        url = f"{url}/echo"
        headers = {"x-error-response": "1"}
        request_content = b"Hello"
        if isinstance(client, SyncClient):

            def req_content_sync() -> Iterator[bytes]:
                yield b"Hello, World!"
                msg = "Test error"
                raise RuntimeError(msg)

            resp = await asyncio.to_thread(
                client.stream, method, url, headers=headers, content=request_content
            )
            content = b"".join(resp.content)
        else:
            resp = await client.stream(
                method, url, headers=headers, content=request_content
            )
            content = b""
            async for chunk in resp.content:
                content += chunk

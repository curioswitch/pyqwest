from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyqwest import (
    Client,
    Headers,
    HTTPVersion,
    Request,
    Response,
    SyncClient,
    SyncRequest,
    SyncResponse,
    SyncTransport,
    Transport,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


pytestmark = [
    pytest.mark.parametrize("http_scheme", ["http"], indirect=True),
    pytest.mark.parametrize("http_version", ["h2"], indirect=True),
]


async def read_content(content: AsyncIterator[bytes | bytearray | memoryview]) -> bytes:
    body = bytearray()
    async for chunk in content:
        body.extend(chunk)
    return bytes(body)


@pytest.mark.asyncio
async def test_override_request(url: str, transport: SyncTransport | Transport):
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                request = SyncRequest(
                    method="PUT",
                    url=f"{request.url}?override=true",
                    headers=Headers({"x-override": "yes"}),
                    content=b"Goodbye",
                )

                return transport.execute_sync(request)

        client = SyncClient(SyncOverride())

        resp = await asyncio.to_thread(client.post, url, headers, req_content)
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                request = Request(
                    method="PUT",
                    url=f"{request.url}?override=true",
                    headers=Headers({"x-override": "yes"}),
                    content=b"Goodbye",
                )

                return await transport.execute(request)

        client = Client(Override())
        resp = await client.post(url, headers, req_content)

    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PUT"
    assert resp.headers["x-echo-query-string"] == "override=true"
    assert resp.headers["x-echo-x-override"] == "yes"
    assert "x-hello" not in resp.headers
    assert resp.content == b"Goodbye"


@pytest.mark.asyncio
async def test_override_response(url: str, transport: SyncTransport | Transport):
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                return SyncResponse(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=b"Overridden!",
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = SyncClient(SyncOverride())

        def run():
            with client.stream(method, url, headers, req_content) as resp:
                content = b"".join(resp.content)
            return resp, content

        resp, content = await asyncio.to_thread(run)
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                return Response(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=b"Overridden!",
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = Client(Override())
        async with client.stream(method, url, headers, req_content) as resp:
            content = await read_content(resp.content)

    assert resp.status == 201
    assert resp.headers.getall("override-1") == ["yes", "definitely"]
    assert resp.headers["override-2"] == "sure"
    assert resp.http_version == HTTPVersion.HTTP3
    assert content == b"Overridden!"
    assert resp.trailers["final-trailer"] == "bye"


@pytest.mark.asyncio
async def test_override_response_except_content(
    url: str, transport: SyncTransport | Transport
):
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                res = transport.execute_sync(request)
                return SyncResponse(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=res.content,
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = SyncClient(SyncOverride())

        resp = await asyncio.to_thread(client.post, url, headers, req_content)
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                res = await transport.execute(request)
                return Response(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=res.content,
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = Client(Override())
        resp = await client.post(url, headers, req_content)

    assert resp.status == 201
    assert resp.headers.getall("override-1") == ["yes", "definitely"]
    assert resp.headers["override-2"] == "sure"
    assert resp.content == b"Hello, World!"
    assert resp.trailers["final-trailer"] == "bye"


@pytest.mark.asyncio
async def test_override_response_content(
    url: str, transport: SyncTransport | Transport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [("content-type", "text/plain"), ("te", "trailers")]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                response = transport.execute_sync(request)

                def overridden_content() -> Iterator[bytes]:
                    for chunk in response.content:
                        yield b"mark:" + chunk

                return SyncResponse(
                    status=response.status,
                    http_version=response.http_version,
                    headers=response.headers,
                    content=overridden_content(),
                    trailers=response.trailers,
                )

        client = SyncClient(SyncOverride())

        def run():
            with client.stream(method, url, headers, req_content) as resp:
                content = b"".join(resp.content)
            return resp, content

        resp, content = await asyncio.to_thread(run)
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                response = await transport.execute(request)

                async def overridden_content() -> AsyncIterator[bytes]:
                    async for chunk in response.content:
                        yield b"mark:" + chunk

                return Response(
                    status=response.status,
                    http_version=response.http_version,
                    headers=response.headers,
                    content=overridden_content(),
                    trailers=response.trailers,
                )

        client = Client(Override())
        async with client.stream(method, url, headers, req_content) as resp:
            content = await read_content(resp.content)

    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers["x-echo-te"] == "trailers"
    assert resp.http_version == HTTPVersion.HTTP2
    assert content == b"mark:Hello, World!"
    assert resp.trailers["x-echo-trailer"] == "last info"


@pytest.mark.asyncio
async def test_override_response_trailers(
    url: str, transport: SyncTransport | Transport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [("content-type", "text/plain"), ("te", "trailers")]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                response = transport.execute_sync(request)

                return SyncResponse(
                    status=response.status,
                    http_version=response.http_version,
                    headers=response.headers,
                    content=response.content,
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = SyncClient(SyncOverride())

        def run():
            with client.stream(method, url, headers, req_content) as resp:
                content = b"".join(resp.content)
            return resp, content

        resp, content = await asyncio.to_thread(run)
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                response = await transport.execute(request)

                return Response(
                    status=response.status,
                    http_version=response.http_version,
                    headers=response.headers,
                    content=response.content,
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = Client(Override())
        async with client.stream(method, url, headers, req_content) as resp:
            content = await read_content(resp.content)

    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers["x-echo-te"] == "trailers"
    assert resp.http_version == HTTPVersion.HTTP2
    assert content == b"Hello, World!"
    assert resp.trailers["final-trailer"] == "bye"


@pytest.mark.asyncio
async def test_override_response_execute(
    url: str, transport: SyncTransport | Transport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncTransport):

        class SyncOverride(SyncTransport):
            def execute_sync(self, request: SyncRequest) -> SyncResponse:
                return SyncResponse(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=b"Overridden!",
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = SyncClient(SyncOverride())

        resp = await asyncio.to_thread(
            client.execute, method, url, headers, req_content
        )
    else:

        class Override(Transport):
            async def execute(self, request: Request) -> Response:
                return Response(
                    status=201,
                    http_version=HTTPVersion.HTTP3,
                    headers=Headers(
                        (
                            ("override-1", "yes"),
                            ("override-1", "definitely"),
                            ("override-2", "sure"),
                        )
                    ),
                    content=b"Overridden!",
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = Client(Override())
        resp = await client.execute(method, url, headers, req_content)

    assert resp.status == 201
    assert resp.headers.getall("override-1") == ["yes", "definitely"]
    assert resp.headers["override-2"] == "sure"
    assert resp.content == b"Overridden!"
    assert resp.trailers["final-trailer"] == "bye"

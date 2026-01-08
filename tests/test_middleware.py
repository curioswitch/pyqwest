from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from pyqwest import (
    Client,
    Headers,
    HTTPTransport,
    HTTPVersion,
    Request,
    Response,
    SyncClient,
    SyncHTTPTransport,
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


async def read_content(content: AsyncIterator[bytes]) -> bytes:
    body = bytearray()
    async for chunk in content:
        body.extend(chunk)
    return bytes(body)


@pytest.mark.asyncio
async def test_override_request(url: str, transport: HTTPTransport | SyncHTTPTransport):
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncHTTPTransport):

        class SyncOverride(SyncTransport):
            def execute(self, request: SyncRequest) -> SyncResponse:
                request = SyncRequest(
                    method="PUT",
                    url=f"{request.url}?override=true",
                    headers=Headers({"x-override": "yes"}),
                    content=b"Goodbye",
                )

                return transport.execute(request)

        client = SyncClient(SyncOverride())

        resp = await asyncio.to_thread(client.stream, method, url, headers, req_content)
        content = b"".join(resp.content)
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
        resp = await client.stream(method, url, headers, req_content)
        content = await read_content(resp.content)

    assert resp.status == 200
    assert resp.headers["x-echo-method"] == "PUT"
    assert resp.headers["x-echo-query-string"] == "override=true"
    assert resp.headers["x-echo-x-override"] == "yes"
    assert "x-hello" not in resp.headers
    assert content == b"Goodbye"


@pytest.mark.asyncio
async def test_override_response(
    url: str, transport: HTTPTransport | SyncHTTPTransport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncHTTPTransport):

        class SyncOverride(SyncTransport):
            def execute(self, request: SyncRequest) -> SyncResponse:
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

        resp = await asyncio.to_thread(client.stream, method, url, headers, req_content)
        content = b"".join(resp.content)
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
        resp = await client.stream(method, url, headers, req_content)
        content = await read_content(resp.content)

    assert resp.status == 201
    assert resp.headers.getall("override-1") == ["yes", "definitely"]
    assert resp.headers["override-2"] == "sure"
    assert resp.http_version == HTTPVersion.HTTP3
    assert content == b"Overridden!"
    assert resp.trailers["final-trailer"] == "bye"


@pytest.mark.asyncio
async def test_override_response_content(
    url: str, transport: HTTPTransport | SyncHTTPTransport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [("content-type", "text/plain"), ("te", "trailers")]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncHTTPTransport):

        class SyncOverride(SyncTransport):
            def execute(self, request: SyncRequest) -> SyncResponse:
                response = transport.execute(request)

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

        resp = await asyncio.to_thread(client.stream, method, url, headers, req_content)
        content = b"".join(resp.content)
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
        resp = await client.stream(method, url, headers, req_content)
        content = await read_content(resp.content)

    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers["x-echo-te"] == "trailers"
    assert resp.http_version == HTTPVersion.HTTP2
    assert content == b"mark:Hello, World!"
    assert resp.trailers["x-echo-trailer"] == "last info"


@pytest.mark.asyncio
async def test_override_response_trailers(
    url: str, transport: HTTPTransport | SyncHTTPTransport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [("content-type", "text/plain"), ("te", "trailers")]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncHTTPTransport):

        class SyncOverride(SyncTransport):
            def execute(self, request: SyncRequest) -> SyncResponse:
                response = transport.execute(request)

                return SyncResponse(
                    status=response.status,
                    http_version=response.http_version,
                    headers=response.headers,
                    content=response.content,
                    trailers=Headers({"final-trailer": "bye"}),
                )

        client = SyncClient(SyncOverride())

        resp = await asyncio.to_thread(client.stream, method, url, headers, req_content)
        content = b"".join(resp.content)
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
        resp = await client.stream(method, url, headers, req_content)
        content = await read_content(resp.content)

    assert resp.status == 200
    assert resp.headers["x-echo-content-type"] == "text/plain"
    assert resp.headers["x-echo-te"] == "trailers"
    assert resp.http_version == HTTPVersion.HTTP2
    assert content == b"Hello, World!"
    assert resp.trailers["final-trailer"] == "bye"


@pytest.mark.asyncio
async def test_override_response_execute(
    url: str, transport: HTTPTransport | SyncHTTPTransport
):
    method = "POST"
    url = f"{url}/echo"
    headers = [
        ("content-type", "text/plain"),
        ("x-hello", "rust"),
        ("x-hello", "python"),
    ]
    req_content = b"Hello, World!"
    if isinstance(transport, SyncHTTPTransport):

        class SyncOverride(SyncTransport):
            def execute(self, request: SyncRequest) -> SyncResponse:
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

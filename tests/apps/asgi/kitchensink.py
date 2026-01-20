from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, cast

import brotli
import zstd

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, HTTPScope, Scope


async def _echo(
    scope: HTTPScope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    echoed_headers = [
        (f"x-echo-{name.decode()}".encode(), value) for name, value in scope["headers"]
    ]
    echoed_headers.append((b"x-echo-query-string", scope["query_string"]))
    echoed_headers.append((b"x-echo-method", scope["method"].encode()))
    headers_dict = dict(scope["headers"])
    if content_type := headers_dict.get(b"content-type", b""):
        echoed_headers.append((b"content-type", content_type))

    if (extensions := scope["extensions"]) and (
        tls := cast("dict | None", extensions.get("tls"))
    ):
        echoed_headers.append(
            (b"x-echo-tls-client-name", str(tls.get("client_cert_name", "")).encode())
        )

    status = 200
    if st := headers_dict.get(b"x-response-status"):
        status = int(st.decode())

    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": echoed_headers,
            "trailers": True,
        }
    )
    # ASGI requires a body message before sending headers.
    await send({"type": "http.response.body", "body": b"", "more_body": True})
    if headers_dict.get(b"x-error-response"):
        msg = "Error before body"
        raise RuntimeError(msg)
    while True:
        message = await receive()
        match message["type"]:
            case "http.disconnect":
                return
            case "http.request":
                body = message["body"]
                if body == b"reset me":
                    msg = "Error mid-stream"
                    raise RuntimeError(msg)
                if body:
                    await send(
                        {
                            "type": "http.response.body",
                            "body": message["body"],
                            "more_body": True,
                        }
                    )
                if not message["more_body"]:
                    break
    await send({"type": "http.response.body", "body": b"", "more_body": False})
    await send(
        {
            "type": "http.response.trailers",
            "headers": [(b"x-echo-trailer", b"last info")],
            "more_trailers": False,
        }
    )


async def _nihongo(
    scope: HTTPScope, _receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": ((b"x-echo-query-string", scope["query_string"]),),
            "trailers": False,
        }
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


async def _content_encoding(
    scope: HTTPScope, _receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    encoding = dict(scope["headers"]).get(b"accept-encoding", b"").decode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": ((b"content-encoding", encoding.encode()),),
            "trailers": False,
        }
    )
    content = b"Hello World!!!!!"
    match encoding:
        case "br":
            content = brotli.compress(content)
        case "gzip":
            content = gzip.compress(content)
        case "zstd":
            content = zstd.compress(content)
    await send({"type": "http.response.body", "body": content, "more_body": False})


async def _read_all(
    _scope: HTTPScope, _receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    await send(
        {"type": "http.response.start", "status": 200, "headers": (), "trailers": False}
    )
    buf = bytearray()
    while True:
        message = await _receive()
        match message["type"]:
            case "http.disconnect":
                return
            case "http.request":
                body = message["body"]
                if body:
                    buf.extend(body)
                if not message["more_body"]:
                    break
    await send({"type": "http.response.body", "body": bytes(buf), "more_body": False})


async def app(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    assert scope["type"] == "http"  # noqa: S101
    match scope["path"]:
        case "/echo":
            await _echo(scope, receive, send)
        case "/日本語 英語":
            await _nihongo(scope, receive, send)
        case "/content-encoding":
            await _content_encoding(scope, receive, send)
        case "/read_all":
            await _read_all(scope, receive, send)
        case _:
            await send(
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")],
                    "trailers": False,
                }
            )
            await send(
                {"type": "http.response.body", "body": b"Not Found", "more_body": False}
            )

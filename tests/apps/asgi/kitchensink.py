from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, HTTPScope


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
    await send(
        {
            "type": "http.response.start",
            "status": 200,
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


async def app(
    scope: HTTPScope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    match scope["path"]:
        case "/echo":
            await _echo(scope, receive, send)
        case _:
            send(
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

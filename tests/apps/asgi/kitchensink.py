from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, HTTPScope


async def _echo(
    scope: HTTPScope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    echoed_headers = [
        (f"x-echo-{name.decode()}".encode(), value) for name, value in scope["headers"]
    ]
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
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message["body"]
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

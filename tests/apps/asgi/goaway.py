"""An app for the refused-stream tests.

Records every request Envoy dispatches to it, so a test can tell a stream Envoy
discarded from one it served, and holds `/held` responses open until `/release`
so a connection stays open while Envoy retires it.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

_POLL_INTERVAL = 0.005

_seen: list[tuple[str, str, str]] = []
_released = False


async def _read_body(receive: ASGIReceiveCallable) -> bytes:
    body = b""
    while True:
        event = await receive()
        if event["type"] != "http.request":
            return body
        body += event.get("body", b"")
        if not event.get("more_body", False):
            return body


async def _start(send: ASGISendCallable) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
            "trailers": False,
        }
    )


async def _respond(send: ASGISendCallable, body: bytes) -> None:
    await _start(send)
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _held(send: ASGISendCallable) -> None:
    await _start(send)
    # Response headers reach the client with the first chunk.
    await send({"type": "http.response.body", "body": b"held-", "more_body": True})
    # Polled rather than an asyncio.Event: `/release` may be handled on another
    # of Envoy's worker threads, each with its own event loop.
    while not _released:  # noqa: ASYNC110
        await asyncio.sleep(_POLL_INTERVAL)
    await send({"type": "http.response.body", "body": b"done", "more_body": False})


async def _release(send: ASGISendCallable) -> None:
    global _released  # noqa: PLW0603
    _released = True
    await _respond(send, b"released")


async def _reset(send: ASGISendCallable) -> None:
    global _released  # noqa: PLW0603
    _released = False
    _seen.clear()
    await _respond(send, b"reset")


async def app(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    if scope["type"] != "http":
        msg = f"Unsupported scope type: {scope['type']}"
        raise RuntimeError(msg)
    body = await _read_body(receive)
    match scope["path"]:
        case "/seen":
            await _respond(send, json.dumps(_seen).encode())
        case "/release":
            await _release(send)
        case "/reset":
            await _reset(send)
        case "/held":
            _seen.append((scope["method"], scope["path"], body.decode()))
            await _held(send)
        case _:
            _seen.append((scope["method"], scope["path"], body.decode()))
            await _respond(send, b"ok")

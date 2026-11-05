from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import pytest
import sniffio

from pyqwest import Client
from pyqwest.testing import ASGITransport

if TYPE_CHECKING:
    from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope


@pytest.mark.anyio
async def test_lifespan_and_request_run_on_backend(anyio_backend: str) -> None:
    libraries: list[str] = []
    events: list[str] = []

    async def app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        libraries.append(sniffio.current_async_library())
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                events.append(message["type"])
                match message["type"]:
                    case "lifespan.startup":
                        scope["state"]["greeting"] = "hello"
                        await send({"type": "lifespan.startup.complete"})
                    case "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            return
        assert scope["type"] == "http"
        body = scope["state"]["greeting"].encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
                "trailers": False,
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async with ASGITransport(app) as transport:
        response = await Client(transport).get("http://localhost/")
        assert response.status == 200
        assert response.content == b"hello"

    assert events == ["lifespan.startup", "lifespan.shutdown"]
    assert libraries == [anyio_backend, anyio_backend]


@pytest.mark.anyio
async def test_cancel_before_response_cancels_app() -> None:
    started = anyio.Event()
    cancelled = anyio.Event()

    async def app(
        _scope: Scope, _receive: ASGIReceiveCallable, _send: ASGISendCallable
    ) -> None:
        started.set()
        try:
            await anyio.sleep_forever()
        except anyio.get_cancelled_exc_class():
            cancelled.set()
            raise

    client = Client(ASGITransport(app))
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(client.get, "http://localhost/")
            await started.wait()
            tg.cancel_scope.cancel()
        await cancelled.wait()

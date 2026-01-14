from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    if sys.version_info >= (3, 11):
        from wsgiref.types import InputStream as WSGIInputStream
        from wsgiref.types import StartResponse, WSGIEnvironment
    else:
        from _typeshed.wsgi import InputStream as WSGIInputStream
        from _typeshed.wsgi import StartResponse, WSGIEnvironment


def _echo(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
    send_trailers: Callable[[list[tuple[str, str]]], None] = environ[
        "wsgi.ext.http.send_trailers"
    ]

    headers = []
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers.append((f"x-echo-{key[5:].replace('_', '-').lower()}", value))
    if ct := environ.get("CONTENT_TYPE"):
        headers.append(("x-echo-content-type", ct))
        headers.append(("content-type", ct))
    if qs := environ.get("QUERY_STRING"):
        headers.append(("x-echo-query-string", qs))
    if method := environ.get("REQUEST_METHOD"):
        headers.append(("x-echo-method", method))
    if client_cert_name := environ.get("wsgi.ext.tls.client_cert_name"):
        headers.append(("x-echo-tls-client-name", client_cert_name))

    start_response("200 OK", headers)

    if environ.get("HTTP_X_ERROR_RESPONSE"):
        msg = "Error before body"
        raise RuntimeError(msg)

    request_body = cast("WSGIInputStream", environ["wsgi.input"])
    while True:
        body = request_body.read(1024)
        if body == b"reset me":
            msg = "Error mid-stream"
            raise RuntimeError(msg)
        if not body:
            break
        yield body

    send_trailers([("x-echo-trailer", "last info")])

from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, cast

import brotli
import zstd

if TYPE_CHECKING:
    import sys
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
            for v in str(value).split(","):
                headers.append((f"x-echo-{key[5:].replace('_', '-').lower()}", v))  # noqa: PERF401
    if ct := environ.get("CONTENT_TYPE"):
        headers.append(("x-echo-content-type", ct))
        headers.append(("content-type", ct))
    if qs := environ.get("QUERY_STRING"):
        headers.append(("x-echo-query-string", qs))
    if method := environ.get("REQUEST_METHOD"):
        headers.append(("x-echo-method", method))
    if client_cert_name := environ.get("wsgi.ext.tls.client_cert_name"):
        headers.append(("x-echo-tls-client-name", client_cert_name))

    start_response("200 OK", headers)(b"")

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


def _nihongo(
    environ: WSGIEnvironment, start_response: StartResponse
) -> Iterable[bytes]:
    query_string = environ["QUERY_STRING"].encode("latin-1").decode("utf-8")
    start_response("200 OK", [("x-echo-query-string", query_string)])
    yield b""


def _content_encoding(
    environ: WSGIEnvironment, start_response: StartResponse
) -> Iterable[bytes]:
    encoding = environ.get("HTTP_ACCEPT_ENCODING", "")
    start_response("200 OK", [("content-encoding", encoding)])
    content = b"Hello World!!!!!"
    match encoding:
        case "br":
            content = brotli.compress(content)
        case "gzip":
            content = gzip.compress(content)
        case "zstd":
            content = zstd.compress(content)
    return [content]


def _read_all(
    environ: WSGIEnvironment, start_response: StartResponse
) -> Iterable[bytes]:
    request_body = cast("WSGIInputStream", environ["wsgi.input"]).read()
    start_response("200 OK", [])
    yield bytes(request_body)


def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
    path = cast("str", environ["PATH_INFO"]).encode("latin-1").decode("utf-8")
    match path:
        case "/echo":
            return _echo(environ, start_response)
        case "/日本語 英語":
            return _nihongo(environ, start_response)
        case "/content-encoding":
            return _content_encoding(environ, start_response)
        case "/read_all":
            return _read_all(environ, start_response)
        case "/no_start":
            return []
        case _:
            start_response("404 Not Found", [("content-type", "text/plain")])
            return [b"Not Found"]

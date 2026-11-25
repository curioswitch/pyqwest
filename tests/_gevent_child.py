"""Runs sync requests under gevent, for `test_gevent`.

The server runs on the same gevent hub as the client, so it only answers if
the client's waits yield to the hub. Prints `ok` if every check passes.
"""

from __future__ import annotations

from gevent import monkey

monkey.patch_all()

from typing import TYPE_CHECKING, Any  # noqa: E402

import gevent  # noqa: E402
from gevent.pywsgi import WSGIServer  # noqa: E402

from pyqwest import SyncClient, SyncHTTPTransport  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

# Bounds the whole run, so a wait that blocks the hub fails rather than hangs.
RUN_TIMEOUT = 30


def app(
    environ: dict[str, Any], start_response: Callable[..., object]
) -> Iterable[bytes]:
    path = environ["PATH_INFO"]
    if path == "/hang":
        gevent.sleep(RUN_TIMEOUT)
    start_response("200 OK", [("content-type", "text/plain")])
    if path == "/stream":
        return stream()
    if path == "/echo":
        return [environ["wsgi.input"].read()]
    return [b"hello"]


def stream() -> Iterator[bytes]:
    for i in range(3):
        gevent.sleep(0.05)
        yield str(i).encode()


def expect(actual: object, expected: object) -> None:
    if actual != expected:
        msg = f"expected {expected!r}, got {actual!r}"
        raise AssertionError(msg)


def main() -> None:
    # A server name skips looking up the host, which can be slow.
    server = WSGIServer(
        ("127.0.0.1", 0), app, log=None, environ={"SERVER_NAME": "localhost"}
    )
    server.start()
    url = f"http://127.0.0.1:{server.server_port}"

    with SyncHTTPTransport() as transport:
        client = SyncClient(transport)

        expect(client.get(f"{url}/").content, b"hello")

        # Other greenlets keep running while a request waits.
        ticks: list[None] = []

        def tick() -> None:
            for _ in range(5):
                gevent.sleep(0.01)
                ticks.append(None)

        ticker = gevent.spawn(tick)
        with client.stream("GET", f"{url}/stream") as res:
            expect(b"".join(res.content), b"012")
        ticker.join()
        expect(len(ticks), 5)

        # A streamed request body is read on a native thread while the
        # request waits on the hub.
        body = (str(i).encode() for i in range(3))
        expect(client.post(f"{url}/echo", content=body).content, b"012")

        # Many requests at once share the hub.
        jobs = [gevent.spawn(client.get, f"{url}/stream") for _ in range(20)]
        gevent.joinall(jobs, raise_error=True)
        expect([job.value.content for job in jobs], [b"012"] * 20)

        # A timeout interrupts a waiting request, which leaves the client usable.
        interrupted = False
        try:
            with gevent.Timeout(0.1):
                client.get(f"{url}/hang")
        except gevent.Timeout:
            interrupted = True
        expect(interrupted, True)  # noqa: FBT003
        expect(client.get(f"{url}/").content, b"hello")

    server.stop()
    print("ok")  # noqa: T201


if __name__ == "__main__":
    with gevent.Timeout(RUN_TIMEOUT):
        main()

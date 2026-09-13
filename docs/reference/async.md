# Async Client

These APIs work in asyncio applications, including those using an asyncio-compatible event
loop such as uvloop. Every API on this page also supports Trio, so `anyio` works on either
backend. Trio support needs no extra; depending on `pyqwest[trio]` requires Trio 0.25 or
later, since older releases fail to import on Python 3.13. Each request detects asyncio or
Trio when it starts, and its response body is read on the same library. Any other async
library raises `RuntimeError`.

`pyqwest.httpx.AsyncPyqwestTransport` and `pyqwest.testing.ASGITransport` call asyncio on
every request, and `pyqwest.middleware.retry.RetryTransport` waits between attempts with
`asyncio.sleep`, so all three need an asyncio application. For synchronous applications,
use [synchronous](./sync.md) APIs.

::: pyqwest.Client
::: pyqwest.Request
::: pyqwest.Multipart
::: pyqwest.Part
::: pyqwest.Transport
::: pyqwest.HTTPTransport
::: pyqwest.get_default_transport

# Async Client

These APIs work in async applications on asyncio, including asyncio-compatible event loops
such as uvloop, or on Trio, so `anyio` works on either backend. Trio support requires Trio
0.22 or later. Each request detects asyncio or Trio when it starts, and its response body is read
on the same library. Any other async library raises `RuntimeError`.

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

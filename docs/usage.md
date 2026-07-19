---
icon: material/hammer-wrench
---

# Usage

The entrypoint to pyqwest is [`Client`](/api/#pyqwest.Client) for asyncio applications
and [`SyncClient`](/api/#pyqwest.SyncClient) for synchronous applications.

=== "async"

    ```python
    from pyqwest import Client

    client = Client()
    ```

=== "sync"

    ```python
    from pyqwest import SyncClient

    client = SyncClient()
    ```

Clients are lightweight - while we generally expect you'll initialize them once for your application,
it should generally be fine to even create them per-operation. There is no `close` type of method on
a client because they share an application-scoped default transport, which is the actual connection
pool. This should feel familiar to those coming from Go's `net/http`.

With a client, you can use methods corresponding to the HTTP methods, or `execute`, to issue a request
and get back a [full response](/api/#pyqwest.FullResponse).

=== "async"

    ```python
    response = await client.get("https://pyqwest.dev")
    assert response.status == 200
    print(response.text())

    response = await client.post(
        "https://httpbingo.org/post",
        headers={"content-type": "application/text", "user-agent": "pyqwest"},
        content=b"Hello world!",
    )
    print(response.text())
    ```

=== "sync"

    ```python
    response = client.get("https://pyqwest.dev")
    assert response.status == 200
    print(response.text())

    response = client.post(
        "https://httpbingo.org/post",
        headers={"content-type": "application/text", "user-agent": "pyqwest"},
        content=b"Hello world!",
    )
    print(response.text())
    ```

## Transport

The default transport is setup to behave closely to a web browser, using standard root certificates
and having timeouts, TCP keepalive, etc configured in a reasonable way, borrowing from the defaults
of the Go `net/http` package. You may need a custom transport though to configure TLS settings or
timeouts, in which case you create an `HTTPTransport` or `SyncHTTPTransport`. Unlike clients, transports
are heavy, with connection pools. Generally you should only create one per application and ensure it
is closed.

### TLS

The default transport will use standard root certificates that can access sites served via
https in the same way as a browser would. For internal use cases, you may use certificates
issued by a custom certificate authority. You can initialize an [`HTTPTransport`](/api/#pyqwest.HTTPTransport)
or [`SyncHTTPTransport`](/api/#pyqwest.SyncHTTPTransport) with a CA certificate for this case.

=== "async"

    ```python
    import asyncio

    from pathlib import Path

    from pyqwest import Client, HTTPTransport

    ca_cert = asyncio.to_thread(Path("/certs/ca.crt").read)
    async with HTTPTransport(tls_ca_cert=ca_cert) as transport:
        client = Client(transport)
        application = MyApplication(client)
    ```

=== "sync"

    ```python
    from pathlib import Path

    from pyqwest import SyncClient, SyncHTTPTransport

    ca_cert = Path("/certs/ca.crt").read()
    with SyncHTTPTransport(tls_ca_cert=my_cert) as transport:
        client = SyncClient(transport)
        application = MyApplication(client)
    ```

If using mTLS with client certificates, just add `tls_cert` and `tls_key` similarly.

=== "async"

    ```python
    async with HTTPTransport(tls_ca_cert=ca_cert, tls_cert=cert, tls_key=key) as transport:
        client = Client(transport)
        application = MyApplication(client)
    ```

=== "sync"

    ```python
    with SyncHTTPTransport(tls_ca_cert=ca_cert, tls_cert=cert, tls_key=key) as transport:
        client = SyncClient(transport)
        application = MyApplication(client)
    ```

### Middleware

HTTP middleware are themselves just `Transport` implementations that accept another
`Transport` to wrap it. By having the same signature, they can operate on any part of the
request/response lifecycle.

pyqwest includes the following middleware.

#### Retry

Retry middleware automatically reissues requests on errors. The default behavior is to
retry known-safe errors, which include connection errors and transient error responses for
non-idempotent methods. The conditions for determining a request or response is retryable
can be customized by subclassing the middleware class and implementing `should_retry_request`
or `should_retry_response` to fit your needs, for example matching against `request.url`.

=== "async"

    ```python
    from pyqwest import Client, HTTPTransport, Request
        from pyqwest.middleware.retry import RetryTransport


        class MyRetryTransport(RetryTransport):
            def should_retry_request(self, request: Request) -> bool:
                return not request.url.endswith("/unsafe-method")


        client = Client(transport=MyRetryTransport(HTTPTransport()))
        await client.get(
            "http://localhost/safe-method"
        )  # will retry on transient errors
        await client.get("http://localhost/unsafe-method")  # will not retry
    ```

=== "sync"

    ```python
    from pyqwest import SyncClient, SyncHTTPTransport, SyncRequest
        from pyqwest.middleware.retry import SyncRetryTransport


        class MyRetryTransport(SyncRetryTransport):
            def should_retry_request(self, request: SyncRequest) -> bool:
                return not request.url.endswith("/unsafe-method")


        client = SyncClient(transport=MyRetryTransport(SyncHTTPTransport()))
        client.get("http://localhost/safe-method")  # will retry on transient errors
        client.get("http://localhost/unsafe-method")  # will not retry
    ```

### Proxies

The transport can be configured to send all requests through a proxy by passing its URL.
The URL scheme may be `http`, `https`, `socks5`, or `socks5h`. Credentials in the URL
will be used for proxy authentication.

=== "async"

    ```python
    async with HTTPTransport(proxy="http://user:pass@localhost:8030") as transport:
        client = Client(transport)
        application = MyApplication(client)
    ```

=== "sync"

    ```python
    with SyncHTTPTransport(proxy="http://user:pass@localhost:8030") as transport:
        client = SyncClient(transport)
        application = MyApplication(client)
    ```

### Timeouts

The transport can be configured with timeouts for overall operations, connect, and reads.

=== "async"

    ```python
    async with HTTPTransport(timeout=10, connect_timeout=1, read_timeout=0.3) as transport:
        client = Client(transport)
        application = MyApplication(client)
    ```

=== "sync"

    ```python
    with SyncHTTPTransport(timeout=10, connect_timeout=1, read_timeout=0.3) as transport:
        client = SyncClient(transport)
        application = MyApplication(client)
    ```

The overall operation timeout can also be configured per-call to override the transport's
setting. Connect and read timeout cannot be configured per-call.

=== "async"

    ```python
    response = await client.get("https://pyqwest.dev", timeout=2.0)
    ```

=== "sync"

    ```python
    response = client.get("https://pyqwest.dev", timeout=2.0)
    ```

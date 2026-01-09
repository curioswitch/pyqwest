#

<img alt="logo" src="./img/logo.png" width="320" height="320">
/// caption
A Python HTTP client built on reqwest
///

<div style="text-align:center">
<a href="https://opensource.org/licenses/MIT"><img alt="license" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
<a href="https://github.com/curioswitch/pyqwest/actions/workflows/ci.yaml"><img alt="ci" src="https://github.com/curioswitch/pyqwest/actions/workflows/ci.yaml/badge.svg"></a>
<a href="https://codecov.io/github/curioswitch/pyqwest"><img alt="codecov" src="https://codecov.io/github/curioswitch/pyqwest/graph/badge.svg"></a>
<a href="https://pypi.org/project/pyqwest"><img alt="pypi version" src="https://img.shields.io/pypi/v/pyqwest"></a>
</div>

pyqwest is a Python HTTP client supporting modern HTTP features, based on the Rust library [reqwest](https://github.com/seanmonstar/reqwest).
It does not reinvent any features of HTTP or sockets, delegating to the excellent reqwest, which uses hyper, for all core functionality
while presenting a familiar Pythonic API.

## Features

- All features of HTTP, including bidirectional streaming, trailers, and HTTP/3
- Async and sync clients
- The stability and performance of the Rust HTTP client stack
- A fully-typed, Pythonic API - no runtime-checked union types

## Quickstart

pyqwest is available on [PyPI](https://pypi.org/project/pyqwest/) so can be installed using your favorite package manager.

=== "uv"

    ```bash
    uv add pyqwest
    ```

=== "pip"

    ```bash
    pip install pyqwest
    ```

---

The `Client` and `SyncClient` classes can be used to make requests. By default, they will use
a shared connection pool and are safe to use for production.

=== "async"

    ```python
    client = pyqwest.Client()

    response = await client.get("https://curioswitch.org")
    print(len(response.content))
    ```

=== "sync"

    ```python
    client = pyqwest.SyncClient()

    response = client.get("https://curioswitch.org")
    print(len(response.content))
    ```

If you already use HTTPX, you can use initialize clients with `pyqwest.httpx.PyQwestTransport`
or `pyqwest.httpx.AsyncPyQwestTransport` to enable all the features of pyqwest without changing
your business logic.

See the [API reference](https://curioswitch.github.io/pyqwest/api/) for all the APIs available.

## Why pyqwest?

pyqwest was created out of a desire to bring bidirectional streaming and HTTP/2 trailers to Python HTTP
clients to allow using the gRPC protocol with standard applications - it powers the gRPC client functionality
in [connect-python](https://github.com/connectrpc/connect-python). While developing it, we have found it to
be a very fast, stable client for any workload.

# pyqwest

[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![CI](https://github.com/curioswitch/pyqwest/actions/workflows/ci.yaml/badge.svg)](https://github.com/curioswitch/pyqwest/actions/workflows/ci.yaml)
[![codecov](https://codecov.io/github/curioswitch/pyqwest/graph/badge.svg)](https://codecov.io/github/curioswitch/pyqwest)

pyqwest is a Python HTTP client supporting modern HTTP features, based on the Rust library [reqwest](https://github.com/seanmonstar/reqwest).
It does not reinvent any features of HTTP or sockets, delegating to the excellent reqwest, which uses hyper, for all core functionality
while presenting a familiar Pythonic API.

## Features

- All features of HTTP, including bidirectional streaming, trailers, and HTTP/3
- Async and sync clients
- The stability and performance of the Rust HTTP client stack
- A fully-typed, Pythonic API - no runtime-checked union types

## Installation

pyqwest is published to PyPI and can be installed as normal. We publish wheels for a wide variety of
platforms, but if you happen to be using one without prebuilt wheels, it will be built automatically
if you have Rust installed.

```bash
uv add pyqwest # or pip install
```

## Usage

pyqwest provides the classes `Client` and `SyncClient` for async and sync applications respectively.
These are ready to use to issue requests, or you can create and pass `HTTPTransport` or `SyncHTTPTransport`
to configure settings like TLS certificates.

```python
client = pyqwest.Client()

response = await client.get("https://curioswitch.org")
print(len(response.content))
```

See the [API reference](https://curioswitch.github.io/pyqwest/api/) for all the APIs available.

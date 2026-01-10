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

## Benchmarks

We have some [preliminary benchmarks](tests/test_benchmark.py) just to understand how the approach works.
Note that these are essentially microbenchmarks - almost all real-world usage will be dominated by the
server's time and not be significantly affected by the performance of the HTTP client itself.

An example from a macOS laptop, for async (with uvloop) HTTP/2 with no request / response content

```
test_benchmark_async[pyqwest-0-http-h2]            23.8466 (1.0)       28.1299 (1.0)       25.3533 (1.0)      0.7858 (1.0)       25.2118 (1.0)      0.6631 (1.0)           5;3  39.4427 (1.0)          36           1
test_benchmark_async[httpx_pyqwest-0-http-h2]      60.2672 (2.53)      93.4816 (3.32)      63.5238 (2.51)     8.0387 (10.23)     61.4938 (2.44)     1.5876 (2.39)          1;1  15.7421 (0.40)         16           1
test_benchmark_async[httpx-0-http-h2]             180.1868 (7.56)     195.9454 (6.97)     184.1702 (7.26)     6.2000 (7.89)     181.3329 (7.19)     5.8279 (8.79)          1;1   5.4298 (0.14)          6           1
```

Note we see no difference in trends with different content sizes. We see in this microbenchmark that pyqwest
seems to significantly outperform HTTPX. The pyqwest HTTPX adapter also seems to bring potential performance
to projects using HTTPX that do not want to change business logic.

Testing HTTP/1 allows us to also check AIOHTTP

```
test_benchmark_async[aiohttp-0-http-h1]            16.7938 (1.0)       20.7214 (1.0)       18.1937 (1.0)       0.7742 (1.86)      18.1273 (1.0)       1.0262 (1.90)         10;1  54.9642 (1.0)          38           1
test_benchmark_async[pyqwest-0-http-h1]            20.2705 (1.21)      22.2955 (1.08)      21.2425 (1.17)      0.4158 (1.0)       21.2635 (1.17)      0.5392 (1.0)           6;1  47.0754 (0.86)         31           1
test_benchmark_async[httpx_pyqwest-0-http-h1]      54.8534 (3.27)      88.8888 (4.29)      60.6263 (3.33)      9.7253 (23.39)     56.7566 (3.13)      2.4687 (4.58)          2;3  16.4945 (0.30)         18           1
test_benchmark_async[httpx-0-http-h1]             308.5213 (18.37)    333.9165 (16.11)    320.0622 (17.59)    11.2062 (26.95)    317.3413 (17.51)    20.1658 (37.40)         2;0   3.1244 (0.06)          5           1
```

AIOHTTP is comfortably the fastest - if only needing async with HTTP/1, it is an excellent HTTP client.
pyqwest seems to perform fairly closely here too.

We see the same trend for sync as well (note sync outperforms async likely because the benchmark is CPU, not I/O, bound)

```
test_benchmark_sync[pyqwest-0-http-h2]           14.3617 (1.0)       16.8924 (1.0)       14.9348 (1.0)       0.4427 (1.0)      14.8650 (1.0)      0.4911 (1.0)          11;2  66.9576 (1.0)          52           1
test_benchmark_sync[httpx_pyqwest-0-http-h2]     52.0353 (3.62)     100.9097 (5.97)      56.7675 (3.80)     12.0905 (27.31)    52.6683 (3.54)     0.8969 (1.83)          2;4  17.6157 (0.26)         19           1
test_benchmark_sync[httpx-0-http-h2]             97.2703 (6.77)     131.0486 (7.76)     101.3821 (6.79)     10.4656 (23.64)    97.8609 (6.58)     0.8371 (1.70)          1;2   9.8637 (0.15)         10           1
```

As always, performance tends to vary widely based on use cases and environments - it is important to perform your own testing
when concerned about performance for your situation.

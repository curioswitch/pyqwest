from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, cast

import pytest

from pyqwest import Client, SyncClient, SyncHTTPTransport

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics._internal.point import Metric, Sum
    from opentelemetry.test.test_base import TestBase


from ._util import SyncRequestBody


def get_runtime_metrics(otel_test_base: TestBase) -> list[Metric]:
    metrics = cast("list[Metric]", otel_test_base.get_sorted_metrics())
    return [
        metric for metric in metrics if metric.name.startswith("rust.async_runtime.")
    ]


@pytest.mark.order(1)
@pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True)
@pytest.mark.parametrize("client_type", ["async", "sync"])
@pytest.mark.asyncio
async def test_basic(
    client: Client | SyncClient, url: str, otel_test_base: TestBase
) -> None:
    # Make a simple request so metrics are non-zero regardless of if running the test
    # by itself or as part of the full suite.
    url = f"{url}/echo"
    if isinstance(client, Client):
        await client.get(url)
    else:
        await asyncio.to_thread(client.get, url)

    # Tokio takes some time to update internal metrics, unfortunately the best we can do is sleep.
    await asyncio.sleep(0.05)

    metrics = get_runtime_metrics(otel_test_base)
    assert len(metrics) == 5
    alive_tasks = metrics[0]
    assert alive_tasks.name == "rust.async_runtime.alive_tasks.count"
    alive_tasks_data = cast("Sum", alive_tasks.data)
    assert len(alive_tasks_data.data_points) == 1
    # Any usage of tokio will include some management tasks. We try to find
    # a value that checks for task leak.
    assert alive_tasks_data.data_points[0].value < 12
    assert alive_tasks_data.data_points[0].attributes == {"rust.runtime": "tokio"}

    blocking_threads = metrics[1]
    assert blocking_threads.name == "rust.async_runtime.blocking_threads.count"
    blocking_threads_data = cast("Sum", blocking_threads.data)
    assert len(blocking_threads_data.data_points) == 2
    active_blocking_threads = blocking_threads_data.data_points[0]
    assert active_blocking_threads.value == 0
    assert active_blocking_threads.attributes == {
        "rust.runtime": "tokio",
        "rust.thread.state": "active",
    }
    idle_blocking_threads = blocking_threads_data.data_points[1]
    # Assertion to find leaked blocking threads. The actual value is flexible,
    # i.e. if only async tests are ever run, it would be zero, otherwise two
    # sync tests can be run close enough to have an extra thread spawned
    # since Tokio state is largely eventually consistent.
    assert idle_blocking_threads.value <= 10
    assert idle_blocking_threads.attributes == {
        "rust.runtime": "tokio",
        "rust.thread.state": "idle",
    }

    task_queue_depth = metrics[2]
    assert task_queue_depth.name == "rust.async_runtime.task_queue.size"
    task_queue_depth_data = cast("Sum", task_queue_depth.data)
    assert len(task_queue_depth_data.data_points) == 2
    blocking_tasks = task_queue_depth_data.data_points[0]
    assert blocking_tasks.value == 0
    assert blocking_tasks.attributes == {
        "rust.runtime": "tokio",
        "rust.task.type": "blocking",
    }
    global_tasks = task_queue_depth_data.data_points[1]
    assert global_tasks.value == 0
    assert global_tasks.attributes == {
        "rust.runtime": "tokio",
        "rust.task.type": "global",
    }

    worker_busy_time = metrics[3]
    assert worker_busy_time.name == "rust.async_runtime.worker_busy_duration"
    worker_busy_time_data = cast("Sum", worker_busy_time.data)
    assert len(worker_busy_time_data.data_points) == 1
    busy_time_point = worker_busy_time_data.data_points[0]
    assert busy_time_point.value >= 0
    assert busy_time_point.attributes == {"rust.runtime": "tokio"}

    workers = metrics[4]
    assert workers.name == "rust.async_runtime.workers.count"
    workers_data = cast("Sum", workers.data)
    assert len(workers_data.data_points) == 1
    workers_point = workers_data.data_points[0]
    assert workers_point.value >= 2
    assert workers_point.attributes == {"rust.runtime": "tokio"}


@pytest.mark.order(2)
@pytest.mark.parametrize("http_scheme", ["http", "https"], indirect=True)
@pytest.mark.parametrize("http_version", ["h1", "h2"], indirect=True)
@pytest.mark.asyncio
async def test_blocking_threads(
    sync_transport: SyncHTTPTransport, url: str, otel_test_base: TestBase
) -> None:
    client = SyncClient(sync_transport)
    concurrency = 32
    request_sent = Barrier(concurrency + 1)
    finish_request = threading.Event()

    url = f"{url}/echo"

    def work() -> None:
        request = SyncRequestBody()
        with client.stream("POST", url, content=request) as res:
            assert res.status == 200
            request_sent.wait()
            finish_request.wait()
            # Don't explicitly close request. We want to make sure there are no
            # thread leaks when the response closes it.

    futs: list[Future] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for _ in range(concurrency):
            fut = executor.submit(work)
            futs.append(fut)

        request_sent.wait()

        metrics = get_runtime_metrics(otel_test_base)
        blocking_threads = metrics[1]
        assert blocking_threads.name == "rust.async_runtime.blocking_threads.count"
        blocking_threads_data = cast("Sum", blocking_threads.data)
        assert len(blocking_threads_data.data_points) == 2
        active_blocking_threads = blocking_threads_data.data_points[0]
        assert active_blocking_threads.value >= 32
        assert active_blocking_threads.attributes == {
            "rust.runtime": "tokio",
            "rust.thread.state": "active",
        }
        idle_blocking_threads = blocking_threads_data.data_points[1]
        assert idle_blocking_threads.value == 0
        assert idle_blocking_threads.attributes == {
            "rust.runtime": "tokio",
            "rust.thread.state": "idle",
        }

        finish_request.set()
        for fut in futs:
            fut.result()

        # Tokio takes some time to update internal metrics, unfortunately the best we can do is sleep.
        await asyncio.sleep(0.05)

        metrics = get_runtime_metrics(otel_test_base)
        blocking_threads = metrics[1]
        assert blocking_threads.name == "rust.async_runtime.blocking_threads.count"
        blocking_threads_data = cast("Sum", blocking_threads.data)
        assert len(blocking_threads_data.data_points) == 2
        active_blocking_threads = blocking_threads_data.data_points[0]
        assert active_blocking_threads.value == 0
        assert active_blocking_threads.attributes == {
            "rust.runtime": "tokio",
            "rust.thread.state": "active",
        }
        idle_blocking_threads = blocking_threads_data.data_points[1]
        assert idle_blocking_threads.value >= 32
        assert idle_blocking_threads.attributes == {
            "rust.runtime": "tokio",
            "rust.thread.state": "idle",
        }

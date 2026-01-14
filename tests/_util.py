from __future__ import annotations

from collections.abc import Iterator
from queue import Empty, Queue


class SyncRequestBody(Iterator[bytes]):
    _queue: Queue[bytes | None]

    def __init__(self) -> None:
        self._queue = Queue()
        self._closed = False
        self._pending_read = False

    def __iter__(self) -> Iterator[bytes]:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        while True:
            self._pending_read = True
            try:
                item = self._queue.get(timeout=0.01)
                break
            except Empty:
                if self._closed:
                    item = None
                    break
        self._pending_read = False

        if item is None:
            raise StopIteration
        return item

    def put(self, item: bytes) -> None:
        self._queue.put(item)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)

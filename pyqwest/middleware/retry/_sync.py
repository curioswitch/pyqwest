from __future__ import annotations

import time
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, SyncTransport
from pyqwest._pyqwest import SyncRequest, SyncResponse, _Backoff

from ._shared import (
    default_should_retry_request,
    default_should_retry_response,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class SyncRetryTransport(SyncTransport):
    _transport: SyncTransport
    _initial_interval: float
    _randomization_factor: float
    _multiplier: float
    _max_interval: float
    _max_retries: int

    def __init__(
        self,
        transport: SyncTransport,
        initial_interval: float = 0.5,
        randomization_factor: float = 0.5,
        multiplier: float = 1.5,
        max_interval: float = 60.0,
        max_retries: int = 4,
    ) -> None:
        self._transport = transport
        self._initial_interval = initial_interval
        self._randomization_factor = randomization_factor
        self._multiplier = multiplier
        self._max_interval = max_interval
        self._max_retries = max_retries

    @final
    def execute_sync(self, request: SyncRequest) -> SyncResponse:
        if not self.should_retry_request(request):
            return self._transport.execute_sync(request)

        backoff = _Backoff(
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

        content: bytes | bytearray | None = None

        def initial_request_content() -> Iterator[bytes]:
            nonlocal content
            for chunk in request.content:
                match content:
                    case None:
                        content = chunk
                    case bytes():
                        content = bytearray(content)
                        content.extend(chunk)
                    case bytearray():
                        content.extend(chunk)
                yield chunk

        resp: SyncResponse | Exception

        try:
            resp = self._transport.execute_sync(
                SyncRequest(
                    method=request.method,
                    url=request.url,
                    headers=request.headers,
                    content=initial_request_content(),
                )
            )
        except Exception as e:
            resp = e

        retries = 0
        while True:
            if not self.should_retry_response(request, resp):
                break
            if isinstance(resp, SyncResponse):
                resp.close()
            retries += 1
            if retries > self._max_retries:
                msg = f"Maximum retry attempts exceeded: {self._max_retries}"
                raise ReadError(msg)

            if (
                isinstance(resp, SyncResponse)
                and resp.status == HTTPStatus.TOO_MANY_REQUESTS
                and (
                    wt := parse_retry_after(
                        resp.headers.get(HTTPHeaderName.RETRY_AFTER)
                    )
                )
                is not None
            ):
                wait_time = wt
            else:
                wait_time = backoff.next_backoff()
            if wait_time is None:
                break

            time.sleep(wait_time)

            try:
                resp = self._transport.execute_sync(
                    SyncRequest(
                        method=request.method,
                        url=request.url,
                        headers=request.headers,
                        content=content,
                    )
                )
            except Exception as e:
                resp = e

        if isinstance(resp, Exception):
            raise resp
        return resp

    def should_retry_request(self, request: SyncRequest) -> bool:
        return default_should_retry_request(request.method)

    def should_retry_response(
        self, request: SyncRequest, response: SyncResponse | Exception
    ) -> bool:
        if isinstance(response, Exception):
            return True
        return default_should_retry_response(response.status)

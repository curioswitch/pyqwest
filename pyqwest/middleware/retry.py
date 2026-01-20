from __future__ import annotations

import asyncio

from _pyqwest import _Backoff

from pyqwest import Transport
from pyqwest._pyqwest import Request, Response


class RetryTransport(Transport):
    _transport: Transport
    _initial_interval: float
    _randomization_factor: float
    _multiplier: float
    _max_interval: float
    _max_elapsed_time: float | None

    def __init__(
        self,
        transport: Transport,
        initial_interval: float = 0.5,
        randomization_factor: float = 0.5,
        multiplier: float = 1.5,
        max_interval: float = 60.0,
        max_elapsed_time: float | None = None,
    ) -> None:
        self._transport = transport
        self._initial_interval = initial_interval
        self._randomization_factor = randomization_factor
        self._multiplier = multiplier
        self._max_interval = max_interval
        self._max_elapsed_time = max_elapsed_time

    async def execute(self, request: Request) -> Response:
        return await self.execute_with_retry(
            request,
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

    async def execute_with_retry(
        self,
        request: Request,
        initial_interval: float,
        randomization_factor: float,
        multiplier: float,
        max_interval: float,
    ) -> Response:
        backoff = _Backoff(
            initial_interval, randomization_factor, multiplier, max_interval
        )
        while True:
            resp = await self._transport.execute(request)
            if resp.status < 500:
                return resp
            backoff_time = backoff.next_backoff()
            if backoff_time is None:
                msg = "maximum retry time exceeded"
                raise TimeoutError(msg)
            await asyncio.sleep(backoff_time)

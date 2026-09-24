from __future__ import annotations

import asyncio
import contextlib
import weakref
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, Transport
from pyqwest._pyqwest import Request, Response, _Backoff

from ._shared import (
    RetryMode,
    default_should_retry_request,
    default_should_retry_response,
    normalize_retry_mode,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable


class RetryTransport(Transport):
    """Retry middleware for async clients.

    Wrap a Transport with this class to allow requests to be automatically retried.
    By default, connection errors are retried for any request, while I/O errors and
    transient 429/5xx responses are retried only for GET, HEAD, PUT, and DELETE.

    The default behavior can be overridden by subclassing this class and overriding the
    `should_retry_request` and `should_retry_response` methods to suit any need.

    Examples:
        ```python
        from pyqwest import Client, HTTPTransport, Request
        from pyqwest.middleware.retry import RetryMode, RetryTransport


        class MyRetryTransport(RetryTransport):
            def should_retry_request(self, request: Request) -> bool | RetryMode:
                if request.url.endswith("/unsafe-method"):
                    return False
                return RetryMode.UNBUFFERED


        client = Client(transport=MyRetryTransport(HTTPTransport()))
        await client.get(
            "http://localhost/safe-method"
        )  # will retry on transient errors
        await client.get("http://localhost/unsafe-method")  # will not retry
        ```
    """

    _transport: Transport
    _initial_interval: float
    _randomization_factor: float
    _multiplier: float
    _max_interval: float
    _max_retries: int

    def __init__(
        self,
        transport: Transport,
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
    async def execute(self, request: Request) -> Response:
        retry_mode = normalize_retry_mode(value=self.should_retry_request(request))
        if retry_mode is None:
            return await self._transport.execute(request)

        backoff = _Backoff(
            self._initial_interval,
            self._randomization_factor,
            self._multiplier,
            self._max_interval,
        )

        get_content: Callable[[], bytes | AsyncIterator[bytes]]

        content = request.content
        content_started = False
        retrying_content: RetryingRequestContent | None = None
        unbuffered_stream = (
            not isinstance(content, bytes) and retry_mode == RetryMode.UNBUFFERED
        )

        async def _close_content() -> None:
            aclose = getattr(content, "aclose", None)
            if aclose is not None:
                await aclose()

        if isinstance(content, bytes):

            def _get_content() -> bytes:
                return content

            get_content = _get_content
        elif unbuffered_stream:

            async def _unbuffered_content() -> AsyncIterator[bytes]:
                nonlocal content_started
                content_started = True
                try:
                    async for chunk in content:
                        yield chunk
                finally:
                    await _close_content()

            get_content = _unbuffered_content
        else:
            retrying_content = RetryingRequestContent(content)
            get_content = retrying_content.get

        resp: Response | Exception

        retries = 0
        # Retry connection errors regardless of retry mode.
        try:
            while True:
                try:
                    resp = await self._transport.execute(
                        Request(
                            method=request.method,
                            url=request.url,
                            headers=request.headers,
                            content=get_content(),
                        )
                    )
                except Exception as e:  # noqa: PERF203
                    if not self.should_retry_response(request, e):
                        raise
                    if unbuffered_stream and content_started:
                        # I/O happened for an unbuffered stream, can't retry.
                        raise
                    if retrying_content is not None and not retrying_content.retryable:
                        # A retry could not send the whole content.
                        raise
                    resp = e
                    retries += 1
                    self._check_retries(retries, e)
                    wait_time = backoff.next_backoff()
                    if wait_time is None:
                        raise
                    await asyncio.sleep(wait_time)
                else:
                    break
        except BaseException:
            if unbuffered_stream and not content_started:
                await _close_content()
            if retrying_content is not None:
                retrying_content.finish(abandoned=True)
            raise

        # Don't retry responses with a streaming request when we can't buffer.
        if unbuffered_stream:
            if not content_started:
                await _close_content()
            if isinstance(resp, Exception):
                raise resp
            return resp

        try:
            while True:
                if not self.should_retry_response(request, resp):
                    break
                if retrying_content is not None and not retrying_content.retryable:
                    # A retry could not send the whole content.
                    break
                if isinstance(resp, Response):
                    await resp.aclose()
                retries += 1
                self._check_retries(retries, resp)

                if (
                    isinstance(resp, Response)
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

                await asyncio.sleep(wait_time)

                try:
                    resp = await self._transport.execute(
                        Request(
                            method=request.method,
                            url=request.url,
                            headers=request.headers,
                            content=get_content(),
                        )
                    )
                except Exception as e:
                    resp = e
        except BaseException:
            if retrying_content is not None:
                retrying_content.finish(abandoned=True)
            raise

        if retrying_content is not None:
            retrying_content.finish(abandoned=isinstance(resp, Exception))
        if isinstance(resp, Exception):
            raise resp
        return resp

    def should_retry_request(self, request: Request) -> bool | RetryMode:
        return default_should_retry_request(request.method)

    def should_retry_response(
        self, request: Request, response: Response | Exception
    ) -> bool:
        return default_should_retry_response(
            request.method,
            response.status if isinstance(response, Response) else response,
        )

    def _check_retries(self, retries: int, resp: Response | Exception) -> None:
        if retries > self._max_retries:
            if isinstance(resp, ConnectionError):
                # Connection errors that don't resolve with retries are better
                # surfaced as-is since they are network issues rather than backend.
                raise resp
            msg = f"Maximum retry attempts exceeded: {self._max_retries}"
            if isinstance(resp, Exception):
                raise ReadError(msg) from resp
            raise ReadError(msg)


class RetryingRequestContent:
    """Request content that every attempt can replay.

    One task reads the source for the whole request, a chunk each time an
    attempt asks for one, so that cancelling an abandoned attempt does not
    cancel a read that the next attempt needs.
    """

    def __init__(self, content: AsyncIterator[bytes]) -> None:
        self._content = content
        self._buffer = bytearray()
        self._done = False
        self._failed = False
        self._reader: asyncio.Task[None] | None = None
        self._read_wanted = asyncio.Event()
        self._waiters: list[asyncio.Future[None]] = []
        self._attempts = 0
        self._attempt_ended = False
        self._final = False
        self._stopped = False

    @property
    def retryable(self) -> bool:
        """Whether a retry can send the whole content.

        False once reading the content has raised or has been stopped, because
        the rest of it is lost.
        """
        return not self._failed

    def get(self) -> AsyncGenerator[bytes, None]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No asyncio loop is running, so execute cannot wait for a second
            # attempt. The only attempt reads the content itself.
            return self._replay_and_read(None)
        self._attempts += 1
        self._attempt_ended = False
        body = self._replay_and_read(self._attempts)
        # A body that is dropped before it is read never runs its finally block.
        finalizer = weakref.finalize(
            body, _call_in_loop, loop, self._end_attempt, self._attempts
        )
        finalizer.atexit = False
        return body

    def finish(self, *, abandoned: bool) -> None:
        """Stop reading the content once the last attempt has no use for it.

        Call this when no attempt will follow. The last attempt is abandoned if
        its response is not returned to the caller.
        """
        self._final = True
        if abandoned or self._attempt_ended:
            self._stop_reading()

    async def _replay_and_read(
        self, attempt: int | None
    ) -> AsyncGenerator[bytes, None]:
        # Attempts share the source, so each replays what the others have read.
        sent = 0
        try:
            while True:
                if self._failed:
                    msg = "Request content cannot be retried after reading it failed"
                    raise RuntimeError(msg)
                if sent < len(self._buffer):
                    chunk = bytes(memoryview(self._buffer)[sent:])
                    sent += len(chunk)
                    yield chunk
                elif self._done:
                    return
                elif attempt is None:
                    await self._read()
                else:
                    await self._next_read()
        finally:
            if attempt is not None:
                self._end_attempt(attempt)

    def _end_attempt(self, attempt: int) -> None:
        if attempt != self._attempts:
            return
        self._attempt_ended = True
        if self._final:
            self._stop_reading()

    def _stop_reading(self) -> None:
        if self._stopped:
            # Cancelling the reader again would interrupt the content's cleanup.
            return
        self._stopped = True
        if not self._done:
            self._failed = True
        if self._reader is not None:
            self._reader.cancel()
        # A task that is cancelled before its first step runs none of its code,
        # so the reader cannot be left to end the waits.
        self._end_read(asyncio.CancelledError())

    def _next_read(self) -> asyncio.Future[None]:
        # Each attempt waits on a future of its own, so that cancelling the
        # attempt cancels neither the read nor another attempt's wait.
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        # A wait that is already listed means a read is wanted or under way,
        # possibly for an attempt that has since been abandoned.
        if not self._waiters:
            self._read_wanted.set()
        self._waiters.append(waiter)
        if self._reader is None:
            self._reader = loop.create_task(
                self._read_when_wanted(), name="pyqwest retry content reader"
            )
        return waiter

    async def _read_when_wanted(self) -> None:
        error: BaseException | None = None
        try:
            while True:
                await self._read_wanted.wait()
                self._read_wanted.clear()
                await self._read()
                # Content can catch the cancellation and return a chunk.
                if self._done or self._stopped:
                    return
                self._end_read(None)
        except BaseException as e:
            error = e
            if not isinstance(e, Exception):
                raise
            # The attempts that are waiting raise it. Nothing awaits this task.
        finally:
            if not self._done:
                # Whatever ended the reader, nothing will read the rest.
                self._failed = True
            self._end_read(error)
            # Content that is left at a yield would otherwise be closed by the
            # garbage collector, in another task.
            aclose = getattr(self._content, "aclose", None)
            if aclose is not None and not self._done:
                await aclose()

    async def _read(self) -> None:
        try:
            chunk = await anext(self._content)
            self._buffer.extend(chunk)
        except StopAsyncIteration:
            self._done = True
        except BaseException:
            # Includes a chunk that cannot be buffered.
            self._failed = True
            raise

    def _end_read(self, error: BaseException | None) -> None:
        waiters, self._waiters = self._waiters, []
        for waiter in waiters:
            if waiter.done():
                continue
            if error is None:
                waiter.set_result(None)
            elif isinstance(error, asyncio.CancelledError):
                waiter.cancel()
            else:
                waiter.set_exception(error)


def _call_in_loop(
    loop: asyncio.AbstractEventLoop, callback: Callable[[int], None], attempt: int
) -> None:
    # The transport can drop a body on a thread of its own. Calling into a
    # closed loop raises, and by then the reader cannot run either.
    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(callback, attempt)

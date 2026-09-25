from __future__ import annotations

import weakref
from http import HTTPStatus
from typing import TYPE_CHECKING, final

from pyqwest import HTTPHeaderName, ReadError, Transport
from pyqwest._pyqwest import Request, Response, _Backoff
from pyqwest._runtime import current_runtime

from ._shared import (
    RetryMode,
    default_should_retry_request,
    default_should_retry_response,
    normalize_retry_mode,
    parse_retry_after,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable

    from pyqwest._runtime import Event, Runtime, Task


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

        runtime = current_runtime()
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
            retrying_content = RetryingRequestContent(content, runtime)
            get_content = retrying_content.get

        resp: Response | Exception

        retries = 0
        returned = False
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
                    await runtime.sleep(wait_time)
                else:
                    break

            # Don't retry responses with a streaming request when we can't buffer.
            if unbuffered_stream:
                if isinstance(resp, Exception):
                    raise resp
                returned = True
                return resp

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

                await runtime.sleep(wait_time)

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

            if isinstance(resp, Exception):
                raise resp
            returned = True
            return resp
        finally:
            if unbuffered_stream and not content_started:
                await _close_content()
            if retrying_content is not None:
                retrying_content.finish(abandoned=not returned)

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

    A task reads the source for the whole request, a chunk each time an
    attempt asks for one, so that cancelling an attempt does not cancel a read
    that the next attempt needs.
    """

    def __init__(
        self, content: AsyncIterator[bytes], runtime: Runtime | None = None
    ) -> None:
        self._content = content
        self._runtime = runtime if runtime is not None else current_runtime()
        self._buffer = bytearray()
        self._done = False
        self._failed = False
        self._reader: Task | None = None
        self._read_wanted: Event = self._runtime.new_event()
        # Set when the wanted read ends. A read that raised leaves its error for
        # the attempts that waited on it.
        self._read_done: Event = self._runtime.new_event()
        self._read_error: Exception | None = None
        self._reading = False
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
        self._attempts += 1
        self._attempt_ended = False
        body = self._replay_and_read(self._attempts)
        # A body that is dropped before it is read never runs its finally block.
        # The transport can drop it on a thread of its own.
        finalizer = weakref.finalize(
            body, self._runtime.call_soon_threadsafe, self._end_attempt, self._attempts
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

    async def _replay_and_read(self, attempt: int) -> AsyncGenerator[bytes, None]:
        # Attempts share the source, so each replays what the others have read.
        sent = 0
        try:
            while True:
                if self._failed:
                    # An abandoned attempt's body raises its cancellation
                    # instead, whether or not it has run yet.
                    await self._runtime.checkpoint()
                    msg = "Request content cannot be retried after reading it failed"
                    raise RuntimeError(msg)
                if sent < len(self._buffer):
                    chunk = bytes(memoryview(self._buffer)[sent:])
                    sent += len(chunk)
                    yield chunk
                elif self._done:
                    return
                else:
                    await self._next_read()
                    if self._read_error is not None:
                        raise self._read_error
        finally:
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
        # An asyncio task that is cancelled before its first step runs none of
        # its code, so the reader cannot be left to end the wait.
        self._end_read(None)

    async def _next_read(self) -> None:
        # Attempts wait on an event, so that cancelling one cancels neither the
        # read nor another attempt's wait. A read that is already wanted or
        # under way, possibly for an attempt that has since been abandoned,
        # serves every attempt that waits.
        if not self._reading:
            self._reading = True
            self._read_done = self._runtime.new_event()
            self._read_wanted.set()
            if self._reader is None:
                self._reader = self._runtime.spawn(
                    self._read_when_wanted, "pyqwest retry content reader"
                )
        await self._read_done.wait()

    async def _read_when_wanted(self) -> None:
        error: BaseException | None = None
        try:
            while True:
                await self._read_wanted.wait()
                self._read_wanted = self._runtime.new_event()
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
            # The reader is exiting: cancelling it now would interrupt the
            # content's cleanup.
            self._stopped = True
            if not self._done:
                # Whatever ended the reader, nothing will read the rest.
                self._failed = True
            self._end_read(error)
            # Content that is left at a yield would otherwise be closed by the
            # garbage collector, in another task.
            aclose = getattr(self._content, "aclose", None)
            if aclose is not None and not self._done:
                with self._runtime.shield():
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
        self._reading = False
        if isinstance(error, Exception):
            self._read_error = error
        self._read_done.set()

"""Opt-in HTTP requests own and await their complete execution lifetime."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from anyio import CancelScope
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

EndCallback = Callable[[], Awaitable[None]]


async def finish_execution(
    task: asyncio.Task[None], on_finished: EndCallback | None = None
) -> None:
    with CancelScope(shield=True):
        try:
            if not task.done() and not task.cancelling():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        finally:
            if on_finished is not None:
                await on_finished()


class _OwnedExecutionResponse(StreamingResponse):
    def __init__(
        self,
        response: StreamingResponse,
        task: asyncio.Task[None],
        on_finished: EndCallback | None,
    ) -> None:
        super().__init__(
            response.body_iterator,
            status_code=response.status_code,
            media_type=response.media_type,
            background=response.background,
        )
        self.raw_headers = response.raw_headers
        self._task = task
        self._on_finished = on_finished

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette cancels its stream scope on disconnect; shield cleanup
            # so the tool task cannot continue after the HTTP request finishes.
            await finish_execution(self._task, self._on_finished)


def own_execution(
    response: StreamingResponse,
    task: asyncio.Task[None],
    *,
    on_finished: EndCallback | None = None,
) -> StreamingResponse:
    return _OwnedExecutionResponse(response, task, on_finished)


Result = TypeVar("Result")
DISCONNECT_POLL_SECONDS = 0.1


async def run_until_disconnect(
    operation: Callable[[], Awaitable[Result]], request: Request
) -> Result:
    """Non-stream responses need a disconnect watcher while the model runs."""
    if await request.is_disconnected():
        raise asyncio.CancelledError("Caller disconnected before execution")
    finished = asyncio.Event()

    async def execute() -> Result:
        return await operation()

    async def watch() -> None:
        while not finished.is_set():
            if await request.is_disconnected():
                return
            await asyncio.sleep(DISCONNECT_POLL_SECONDS)

    task, watcher = asyncio.create_task(execute()), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait(
            {task, watcher}, return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in done:
            # Watcher failure also stops work; never assume the caller is alive.
            await watcher
            raise asyncio.CancelledError("Caller disconnected during execution")
        return await task
    finally:
        with CancelScope(shield=True):
            # Request.is_disconnected uses its own cancellation scope; it can
            # absorb a concurrent task.cancel(). The flag still ends the loop.
            finished.set()
            for owned in (task, watcher):
                if not owned.done() and not owned.cancelling():
                    owned.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)

"""Await a non-stream execution and its cleanup when the HTTP caller leaves."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from anyio import CancelScope
from starlette.requests import Request

Result = TypeVar("Result")


async def run_until_disconnect(operation: Callable[[], Awaitable[Result]], request: Request) -> Result:
    if await request.is_disconnected():
        raise asyncio.CancelledError("Caller disconnected before execution")
    finished = asyncio.Event()

    async def execute() -> Result:
        return await operation()

    async def watch() -> None:
        while not finished.is_set():
            if await request.is_disconnected():
                return
            await asyncio.sleep(0.1)

    task, watcher = asyncio.create_task(execute()), asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            await watcher
            raise asyncio.CancelledError("Caller disconnected during execution")
        return await task
    finally:
        with CancelScope(shield=True):
            finished.set()
            for owned in (task, watcher):
                if not owned.done() and not owned.cancelling():
                    owned.cancel()
            await asyncio.gather(task, watcher, return_exceptions=True)

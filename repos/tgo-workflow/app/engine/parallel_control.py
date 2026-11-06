"""Await branch work and a bounded stop check without leaving child tasks."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

Result = TypeVar("Result")
CONTROL_INTERVAL_SECONDS = 0.25


async def await_parallel_branches(
    tasks: Sequence[asyncio.Task[Result]],
    timeout_seconds: int,
    check_active: Callable[[], Awaitable[None]] | None = None,
) -> list[Result]:
    stopped = asyncio.Event()

    async def monitor() -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(
                    stopped.wait(), CONTROL_INTERVAL_SECONDS
                )
                return
            except asyncio.TimeoutError:
                pass
            if check_active is not None:
                await check_active()

    group = asyncio.gather(*tasks)
    watcher = (
        asyncio.create_task(monitor()) if check_active is not None else None
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            if watcher is not None:
                done, _ = await asyncio.wait(
                    (group, watcher),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if watcher in done:
                    await watcher
            return list(await group)
    finally:
        # A control check shares the callback's DB session. Let an in-progress
        # bounded check finish instead of cancelling its transaction mid-query.
        stopped.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if not group.done():
            group.cancel()
        await asyncio.gather(
            group,
            *tasks,
            *([watcher] if watcher is not None else []),
            return_exceptions=True,
        )

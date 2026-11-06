"""Check upstream admission before sending downstream streaming headers."""

from collections.abc import AsyncGenerator
import asyncio

from anyio import CancelScope
from fastapi import HTTPException

ADMISSION_TIMEOUT_SECONDS = 30


async def prepare_workflow_stream(
    upstream: AsyncGenerator[str, None]
) -> AsyncGenerator[str, None]:
    try:
        first = await asyncio.wait_for(
            anext(upstream), timeout=ADMISSION_TIMEOUT_SECONDS
        )
    except BaseException as error:
        with CancelScope(shield=True):
            await upstream.aclose()
        if isinstance(error, StopAsyncIteration):
            raise HTTPException(502, "工作流服务返回了空数据流，请重试。") from None
        if isinstance(error, TimeoutError):
            raise HTTPException(504, "等待工作流服务响应超时，请重试。") from None
        raise

    async def relay() -> AsyncGenerator[str, None]:
        try:
            yield first
            async for frame in upstream:
                yield frame
        finally:
            with CancelScope(shield=True):
                await upstream.aclose()

    return relay()

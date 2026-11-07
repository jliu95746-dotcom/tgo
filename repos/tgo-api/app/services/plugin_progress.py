"""Relay plugin progress without changing SSE frames or exposing errors."""

from collections.abc import AsyncIterator, Mapping
import json

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.services.plugin_runtime_client import QueryValue

logger = get_logger("services.plugin_progress")


def _error_event(message: str) -> bytes:
    return (
        "data: " + json.dumps({"stage": "error", "message": message}) + "\n\n"
    ).encode()


async def stream_plugin_progress(
    path: str,
    payload: Mapping[str, object],
    *,
    params: Mapping[str, QueryValue] | None = None,
) -> AsyncIterator[bytes]:
    """Keep long-running reads while bounding connection and request waits."""
    timeout = httpx.Timeout(
        None,
        connect=settings.PLUGIN_RUNTIME_TIMEOUT,
        write=settings.PLUGIN_RUNTIME_TIMEOUT,
        pool=settings.PLUGIN_RUNTIME_TIMEOUT,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                settings.PLUGIN_RUNTIME_URL.rstrip("/") + path,
                json=payload,
                params=params,
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    yield _error_event(
                        f"Runtime error: {response.status_code}"
                    )
                    return
                content_type = response.headers.get(
                    "Content-Type", ""
                ).split(";", 1)[0]
                if content_type.strip().lower() != "text/event-stream":
                    yield _error_event(
                        "Plugin runtime returned an invalid progress response"
                    )
                    return
                async for chunk in response.aiter_bytes():
                    yield chunk
    except Exception as error:
        logger.error(
            "Plugin progress stream failed",
            extra={"error_type": type(error).__name__},
        )
        yield _error_event("Plugin runtime service unavailable")

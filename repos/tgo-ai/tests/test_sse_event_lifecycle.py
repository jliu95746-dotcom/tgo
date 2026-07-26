from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.streaming.event_emitter import get_active_emitters, get_event_emitter
from app.streaming.sse_handler import create_sse_response


@pytest.mark.asyncio
async def test_sse_response_owns_event_emitter_cleanup() -> None:
    """The producer must leave queued terminal events for the SSE consumer."""

    request_id = "request-lifecycle"
    correlation_id = "correlation-lifecycle"
    emitter = get_event_emitter(request_id, correlation_id)
    emitter.enable_streaming()
    request = AsyncMock()
    request.is_disconnected = AsyncMock(return_value=False)

    response = create_sse_response(emitter, request)

    assert f"{request_id}:{correlation_id}" in get_active_emitters()
    assert response.background is not None

    await response.background()

    assert f"{request_id}:{correlation_id}" not in get_active_emitters()

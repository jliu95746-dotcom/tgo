"""Operator access and upstream failures for WeCom stored work."""

from datetime import datetime, timezone
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import SecretStr

from app.api.v1.endpoints import operations_tasks
from app.api.v1.endpoints.operations import require_operator
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.schemas.wecom_durable_status import (
    DurableCounts, FailureNoticeCounts, WeComDurableStatus,
)
from app.services import platform_message_client


@pytest.mark.asyncio
async def test_durable_status_requires_operator_before_upstream_call(
    monkeypatch,
):
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    called = False

    async def fake_read() -> WeComDurableStatus:
        nonlocal called
        called = True
        return WeComDurableStatus(
            checked_at=datetime.now(timezone.utc),
            inbox=DurableCounts(
                queued=2, processing=0, terminal_failure=0,
                completed=0, other=0, stale_lease=0,
            ),
            media_jobs=DurableCounts(
                queued=0, processing=0, terminal_failure=0,
                completed=0, other=0, stale_lease=0,
            ),
            failure_notices=FailureNoticeCounts(
                pending=0, sending=0, submitted=0,
                unknown=1, needs_attention=0,
            ),
        )

    monkeypatch.setattr(
        operations_tasks, "read_wecom_durable_status", fake_read
    )
    application = FastAPI()
    application.add_exception_handler(
        TGOAPIException, tgo_api_exception_handler
    )
    application.include_router(operations_tasks.router, prefix="/ops")
    application.dependency_overrides[get_db] = lambda: Mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        denied = await client.get("/ops/wecom-durable-status")
        assert denied.status_code in {401, 403}
        assert called is False
        application.dependency_overrides[require_operator] = lambda: Mock()
        allowed = await client.get("/ops/wecom-durable-status")
    assert allowed.status_code == 200
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.json()["inbox"]["queued"] == 2
    assert allowed.json()["failure_notices"]["unknown"] == 1
    assert called is True


@pytest.mark.asyncio
async def test_durable_status_client_rejects_upstream_failure_and_bad_shape(
    monkeypatch,
):
    monkeypatch.setattr(
        settings, "SAAS_INTERNAL_TOKEN", SecretStr("test-internal-token")
    )
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        if len(seen_headers) == 1:
            return httpx.Response(503, json={"detail": "database unavailable"})
        if len(seen_headers) == 2:
            return httpx.Response(200, json={
                "checked_at": "2026-10-15T00:00:00Z",
                "inbox": {
                    "queued": 0, "processing": 0, "terminal_failure": 0,
                    "completed": 0, "other": 0, "stale_lease": 0,
                },
                "media_jobs": {
                    "queued": 0, "processing": 0, "terminal_failure": 0,
                    "completed": 0, "other": 0, "stale_lease": 0,
                },
            })
        return httpx.Response(200, json={
            "checked_at": "2026-10-15T00:00:00Z",
            "inbox": {
                "queued": 2, "processing": 0, "terminal_failure": 0,
                "completed": 3, "other": 0, "stale_lease": 0,
            },
            "media_jobs": {
                "queued": 0, "processing": 0, "terminal_failure": 1,
                "completed": 1, "other": 0, "stale_lease": 0,
            },
            "failure_notices": {
                "pending": 0, "sending": 0, "submitted": 0,
                "unknown": 1, "needs_attention": 0,
            },
        })

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        platform_message_client.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            await platform_message_client.read_wecom_durable_status()
        assert error.value.status_code == 503
    status = await platform_message_client.read_wecom_durable_status()
    assert status.inbox.queued == 2
    assert status.media_jobs.terminal_failure == 1
    assert status.failure_notices.unknown == 1
    assert all(
        headers["X-SaaS-Service-Token"] == "test-internal-token"
        for headers in seen_headers
    )

"""Reconciliation identity is taken from the signed-in staff, never the body."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1.endpoints.workflow_reconciliations import (
    router,
    workflow_client,
)
from app.core.security import get_current_active_user


@pytest.mark.asyncio
async def test_staff_scope_preview_binding_and_receipt_lookup(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    approval, execution, workflow = (str(uuid4()) for _ in range(3))
    now = datetime.now(UTC).isoformat()
    item = {
        "id": approval,
        "execution_id": execution,
        "workflow_id": workflow,
        "summary": "Synthetic review",
        "review_digest": "a" * 64,
        "created_at": now,
        "requested_at": now,
        "can_continue": True,
        "blocked_reason": None,
        "nodes": [],
    }
    result = {
        "id": approval,
        "execution_id": execution,
        "decision": "continue",
        "status": "pending_resume",
        "decided_at": now,
        "dispatch_status": "queued",
    }
    methods = {
        "list_reconciliations": AsyncMock(return_value=[item]),
        "get_reconciliation": AsyncMock(return_value=item),
        "decide_reconciliation": AsyncMock(return_value=result),
        "get_reconciliation_decision": AsyncMock(return_value=result),
    }
    for name, method in methods.items():
        monkeypatch.setattr(workflow_client, name, method)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/v1/ai/workflows/reconciliations"
        response = await client.get(
            path,
            params={
                "project_id": str(uuid4()),
                "actor_staff_id": str(uuid4()),
            },
        )
        assert (
            response.status_code == 200
            and response.headers["cache-control"] == "no-store"
        )
        assert (
            await client.get(path, params={"before_id": approval})
        ).status_code == 400
        assert (await client.get(f"{path}/{approval}")).status_code == 200
        for payload in [
            {"decision": "continue"},
            {"decision": "continue", "review_digest": "x" * 64},
            {
                "decision": "continue",
                "review_digest": "a" * 64,
                "output": "fake",
            },
            {
                "decision": "stop",
                "review_digest": "a" * 64,
                "actor_staff_id": str(uuid4()),
            },
        ]:
            assert (
                await client.post(f"{path}/{approval}/decision", json=payload)
            ).status_code == 422
        response = await client.post(
            f"{path}/{approval}/decision",
            json={"decision": "continue", "review_digest": "a" * 64},
        )
        assert (
            response.status_code == 200
            and response.json()["dispatch_status"] == "queued"
        )
        assert (
            await client.get(f"{path}/{approval}/decisions/" + "a" * 64)
        ).status_code == 200
        assert (
            await client.get(f"{path}/{approval}/decisions/invalid")
        ).status_code == 422
    methods["list_reconciliations"].assert_awaited_once_with(
        str(staff.project_id),
        str(staff.id),
        limit=30,
        before_requested_at=None,
        before_id=None,
    )
    for name in [
        "get_reconciliation",
        "decide_reconciliation",
        "get_reconciliation_decision",
    ]:
        assert methods[name].await_count == 1
        assert methods[name].call_args.args[:3] == (
            str(staff.project_id),
            str(staff.id),
            approval,
        )


@pytest.mark.asyncio
async def test_client_requires_internal_key_and_rejects_malformed_responses(
    monkeypatch,
):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "")
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    with pytest.raises(HTTPException) as error:
        await workflow_client.list_reconciliations("project", "staff")
    assert error.value.status_code == 503
    send.assert_not_awaited()
    monkeypatch.setattr(
        "app.services.workflow_client.settings.SECRET_KEY",
        "synthetic-internal-key",
    )
    monkeypatch.setattr(
        workflow_client, "_handle_response", AsyncMock(return_value=[])
    )
    assert await workflow_client.list_reconciliations("project", "staff") == []
    assert send.call_args.kwargs["extra_headers"] == {
        "X-Internal-API-Key": "synthetic-internal-key"
    }
    marker = "synthetic-private-response-marker"
    for value in [{"private": marker}, [{"private": marker}], [None], [1]]:
        monkeypatch.setattr(
            workflow_client, "_handle_response", AsyncMock(return_value=value)
        )
        with pytest.raises(HTTPException) as error:
            await workflow_client.list_reconciliations("project", "staff")
        assert error.value.status_code == 502
        assert marker not in str(error.value.detail)

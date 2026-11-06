"""Private review API binds scope, current staff, preview and dispatch."""

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from app.api import reconciliations
from app.config import settings
from app.database import get_db
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from tests.unit.test_approval_reconciliation import interrupted
from app.services.workflow_dispatch import WorkflowResumeDispatchError
from app.api.workflow_internal_auth import require_internal_key
from fastapi import HTTPException


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "completed,choice,dispatch_failed",
    [
        (True, "continue", False),
        (False, "stop", False),
        (True, "continue", True),
    ],
)
async def test_private_review_paths_and_single_dispatch(
    monkeypatch, completed, choice, dispatch_failed
):
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("test-review-api-" + "a" * 48)
    )
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=completed)
    app = FastAPI()
    app.include_router(reconciliations.router, prefix="/v1/workflows")

    async def database():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    verify = AsyncMock()
    dispatch = AsyncMock(
        side_effect=WorkflowResumeDispatchError() if dispatch_failed else None
    )
    monkeypatch.setattr(reconciliations, "verify_approval_assignees", verify)
    monkeypatch.setattr(reconciliations, "enqueue_approved_resume", dispatch)
    headers = {"X-Internal-API-Key": settings.SECRET_KEY.get_secret_value()}
    scope = {"project_id": project, "actor_staff_id": actor}
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = "/v1/workflows/approvals/reconciliations"
            assert (await client.get(path, params=scope)).status_code == 403
            for forged in [
                {"project_id": str(uuid4()), "actor_staff_id": actor},
                {"project_id": project, "actor_staff_id": str(uuid4())},
            ]:
                assert (
                    await client.get(path, params=forged, headers=headers)
                ).json() == []
            response = await client.get(path, params=scope, headers=headers)
            assert (
                response.status_code == 200
                and response.headers["cache-control"] == "no-store"
            )
            (item,) = response.json()
            assert (
                "resume_checkpoint" not in item
                and "reconciliation_ledger" not in item
            )
            assert all(
                "output" not in node and "input" not in node
                for node in item["nodes"]
            )
            assert (
                await client.get(
                    path,
                    params={**scope, "before_id": approval},
                    headers=headers,
                )
            ).status_code == 409
            detail = await client.get(
                path + "/" + approval, params=scope, headers=headers
            )
            assert detail.json()["review_digest"] == item["review_digest"]
            decision = {
                "decision": choice,
                "review_digest": item["review_digest"],
            }
            assert (
                await client.post(
                    path + "/" + approval + "/decision",
                    params=scope,
                    headers=headers,
                    json={**decision, "output": "fake"},
                )
            ).status_code == 422
            assert (
                await client.post(
                    path + "/" + approval + "/decision",
                    params=scope,
                    headers=headers,
                    json={**decision, "review_digest": "0" * 64},
                )
            ).status_code == 409
            for suffix, query, expected in [
                (
                    "/" + approval,
                    {**scope, "actor_staff_id": str(uuid4())},
                    409,
                ),
                ("/" + approval + "/decisions/invalid", scope, 422),
                (
                    "/" + approval + "/decisions/" + "b" * 64,
                    {**scope, "project_id": str(uuid4())},
                    409,
                ),
            ]:
                assert (
                    await client.get(
                        path + suffix, params=query, headers=headers
                    )
                ).status_code == expected
            receipt_path = (
                path + "/" + approval + "/decisions/" + item["review_digest"]
            )
            assert (
                await client.get(receipt_path, params=scope, headers=headers)
            ).status_code == 404
            response = await client.post(
                path + "/" + approval + "/decision",
                params=scope,
                headers=headers,
                json=decision,
            )
            assert response.status_code == 200
            assert response.json()["dispatch_status"] == (
                "unknown"
                if dispatch_failed
                else "queued"
                if completed
                else "not_applicable"
            )
            assert response.json()["status"] == (
                "pending_resume" if completed else "cancelled"
            )
            assert (
                await client.get(receipt_path, params=scope, headers=headers)
            ).status_code == 200
            assert (
                await client.post(
                    path + "/" + approval + "/decision",
                    params=scope,
                    headers=headers,
                    json=decision,
                )
            ).status_code == 200
        assert dispatch.await_count == int(completed)
        verify.assert_any_await(project, {actor})
        async with sessions() as db:
            request = await db.get(WorkflowApprovalRequest, approval)
            assert len(request.reconciliation_ledger["decisions"]) == 1
            run = await db.get(WorkflowExecution, execution)
            assert run.status == (
                "pending_resume" if completed else "cancelled"
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_revoked_reviewer_cannot_resolve_the_task(monkeypatch):
    monkeypatch.setattr(
        settings, "SECRET_KEY", SecretStr("test-review-revoked-" + "a" * 48)
    )
    (
        engine,
        sessions,
        project,
        execution,
        actor,
        approval,
        _,
    ) = await interrupted(completed=True)
    app = FastAPI()
    app.include_router(reconciliations.router, prefix="/v1/workflows")

    async def database():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(
        reconciliations,
        "verify_approval_assignees",
        AsyncMock(side_effect=PermissionError("revoked")),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/workflows/approvals/reconciliations/"
                + approval
                + "/decision",
                params={"project_id": project, "actor_staff_id": actor},
                headers={
                    "X-Internal-API-Key": settings.SECRET_KEY.get_secret_value()
                },
                json={"decision": "continue", "review_digest": "0" * 64},
            )
            assert response.status_code == 403
        async with sessions() as db:
            run = await db.get(WorkflowExecution, execution)
            request = await db.get(WorkflowApprovalRequest, approval)
            assert (
                run.status == "running"
                and request.reconciliation_ledger["decisions"] == []
            )
    finally:
        await engine.dispose()


def test_missing_internal_key_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", None)
    with pytest.raises(HTTPException) as error:
        require_internal_key("any-synthetic-key")
    assert error.value.status_code == 503

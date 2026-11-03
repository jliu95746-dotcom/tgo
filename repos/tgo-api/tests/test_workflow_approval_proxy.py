"""The approval proxy derives tenant and assignee from the signed-in staff."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints.ai_workflows import router, workflow_client
from app.core.security import get_current_active_user
from app.core.database import get_db
from app.schemas.ai_workflows import WorkflowNodeInput
from app.schemas.workflow_approval import (
    WorkflowAfterSalesIntakeCreate, WorkflowAfterSalesIntakeSummary,
    WorkflowAfterSalesReviewSummary,
)


@pytest.mark.asyncio
async def test_approval_list_and_decision_use_current_staff(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    approval_id, execution_id = str(uuid4()), str(uuid4())
    timestamp = datetime.now(UTC).isoformat()
    preview_digest = "a" * 64
    list_upstream = AsyncMock(return_value=[{
        "id": approval_id, "execution_id": execution_id, "node_id": "approval",
        "summary": "核对售后登记", "preview_digest": preview_digest,
        "status": "pending",
        "requested_at": timestamp, "expires_at": timestamp,
    }])
    decide_upstream = AsyncMock(return_value={
        "id": approval_id, "execution_id": execution_id,
        "status": "approved", "dispatch_status": "queued",
    })
    monkeypatch.setattr(workflow_client, "list_approvals", list_upstream, raising=False)
    monkeypatch.setattr(workflow_client, "decide_approval", decide_upstream, raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        path = "/v1/ai/workflows/approvals"
        result = await client.get(path, params={"project_id": str(uuid4()), "actor_staff_id": str(uuid4())})
        assert result.status_code == 200
        assert result.json()[0]["summary"] == "核对售后登记"
        assert result.json()[0]["preview_digest"] == preview_digest
        forged = await client.post(
            f"{path}/{approval_id}/decision",
            json={"decision": "approve", "preview_digest": preview_digest,
                  "actor_staff_id": str(uuid4())},
        )
        assert forged.status_code == 422
        assert (await client.post(
            f"{path}/{approval_id}/decision", json={"decision": "approve"},
        )).status_code == 422
        result = await client.post(
            f"{path}/{approval_id}/decision",
            json={"decision": "approve", "preview_digest": preview_digest},
        )
        assert result.status_code == 200
        assert result.json()["dispatch_status"] == "queued"
    list_upstream.assert_awaited_once_with(str(staff.project_id), str(staff.id))
    decide_upstream.assert_awaited_once_with(
        str(staff.project_id), str(staff.id), approval_id, "approve", preview_digest,
    )


@pytest.mark.asyncio
async def test_approval_client_sends_internal_key(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=[]))
    assert await workflow_client.list_approvals("project-a", "staff-a") == []
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/approvals",
        params={"project_id": "project-a", "actor_staff_id": "staff-a"},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_aftersales_review_list_uses_current_staff_and_internal_key(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    item = {
        "id": str(uuid4()), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(uuid4()),
        "category": "return_request", "status": "pending_staff_review",
        "review_digest": "a" * 64,
        "created_at": datetime.now(UTC).isoformat(),
    }
    upstream = AsyncMock(return_value=[item])
    monkeypatch.setattr(workflow_client, "list_aftersales_reviews", upstream)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get("/v1/ai/workflows/aftersales-reviews")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()[0]["id"] == item["id"]
    upstream.assert_awaited_once_with(
        str(staff.project_id), str(staff.id), limit=100,
        before_status=None, before_created_at=None, before_id=None,
    )


@pytest.mark.asyncio
async def test_aftersales_review_cursor_is_validated_and_forwarded(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    upstream = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "list_aftersales_reviews", upstream)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    created = datetime(2026, 10, 17, tzinfo=UTC)
    review_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        path = "/v1/ai/workflows/aftersales-reviews"
        assert (await client.get(path, params={"before_id": str(review_id)})).status_code == 400
        assert (await client.get(path, params={"limit": 101})).status_code == 422
        result = await client.get(path, params={
            "limit": 2, "before_status": "reviewed",
            "before_created_at": created.isoformat(), "before_id": str(review_id),
        })
        assert result.status_code == 200, result.text
    upstream.assert_awaited_once_with(
        str(staff.project_id), str(staff.id), limit=2,
        before_status="reviewed", before_created_at=created.isoformat(),
        before_id=str(review_id),
    )


@pytest.mark.asyncio
async def test_aftersales_review_decision_uses_current_staff(monkeypatch):
    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    review_id = uuid4()
    result = {
        "id": str(review_id), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(uuid4()),
        "category": "return_request", "status": "reviewed",
        "review_digest": "a" * 64,
        "review_result": "needs_manual_service", "reviewed_by": str(staff.id),
        "reviewed_at": datetime.now(UTC).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    upstream = AsyncMock(return_value=result)
    monkeypatch.setattr(workflow_client, "decide_aftersales_review", upstream)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        path = f"/v1/ai/workflows/aftersales-reviews/{review_id}/decision"
        payload = {"result": "needs_manual_service", "review_digest": "a" * 64}
        assert (await client.post(path, json={**payload, "actor_staff_id": str(uuid4())})).status_code == 422
        response = await client.post(path, json=payload)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["review_result"] == "needs_manual_service"
    args = upstream.await_args.args
    assert args[:3] == (str(staff.project_id), str(staff.id), str(review_id))
    assert args[3].result == "needs_manual_service"
    assert args[3].review_digest == "a" * 64


@pytest.mark.asyncio
async def test_aftersales_review_client_sends_internal_key(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=[]))
    assert await workflow_client.list_aftersales_reviews("project-a", "staff-a") == []
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/approvals/aftersales-reviews",
        params={"project_id": "project-a", "actor_staff_id": "staff-a",
                "limit": 100},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )
    send.reset_mock()
    await workflow_client.list_aftersales_reviews(
        "project-a", "staff-a", limit=2, before_status="reviewed",
        before_created_at="2026-10-17T00:00:00+00:00", before_id="review-a",
    )
    send.assert_awaited_once_with(
        "GET", "/v1/workflows/approvals/aftersales-reviews",
        params={"project_id": "project-a", "actor_staff_id": "staff-a",
                "limit": 2, "before_status": "reviewed",
                "before_created_at": "2026-10-17T00:00:00+00:00", "before_id": "review-a"},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_aftersales_file_list_uses_trusted_review_session(monkeypatch):
    from app.api.v1.endpoints import ai_workflows

    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    review_id = uuid4()
    session_id = uuid4()
    item = WorkflowAfterSalesReviewSummary.model_validate({
        "id": str(review_id), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(session_id),
        "category": "quality_issue", "status": "pending_staff_review",
        "review_digest": "b" * 64, "created_at": datetime.now(UTC).isoformat(),
    })
    fetch = AsyncMock(return_value=item)
    files = AsyncMock(return_value=[])
    monkeypatch.setattr(workflow_client, "get_aftersales_review", fetch, raising=False)
    monkeypatch.setattr(ai_workflows, "list_review_files", files, raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    database = object()
    app.dependency_overrides[get_db] = lambda: database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/ai/workflows/aftersales-reviews/{review_id}/files",
            params={"visitor_session_id": str(uuid4()), "project_id": str(uuid4())},
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == []
    fetch.assert_awaited_once_with(str(staff.project_id), str(staff.id), str(review_id))
    files.assert_awaited_once_with(database, staff, session_id)


@pytest.mark.asyncio
async def test_aftersales_chat_target_uses_trusted_review_session(monkeypatch):
    from app.api.v1.endpoints import ai_workflows

    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    review_id, session_id, visitor_id = uuid4(), uuid4(), uuid4()
    review = WorkflowAfterSalesReviewSummary.model_validate({
        "id": review_id, "execution_id": uuid4(), "approval_id": uuid4(),
        "visitor_session_id": session_id, "category": "return_request",
        "status": "pending_staff_review", "review_digest": "b" * 64,
        "created_at": datetime.now(UTC).isoformat(),
    })
    fetch = AsyncMock(return_value=review)
    target = AsyncMock(return_value={
        "channel_type": 251, "channel_id": f"{visitor_id}-vtr",
    })
    monkeypatch.setattr(workflow_client, "get_aftersales_review", fetch)
    monkeypatch.setattr(ai_workflows, "review_chat_target", target, raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    database = object()
    app.dependency_overrides[get_db] = lambda: database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/ai/workflows/aftersales-reviews/{review_id}/chat-target",
            params={"visitor_session_id": str(uuid4()), "actor_staff_id": str(uuid4())},
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {
            "channel_type": 251, "channel_id": f"{visitor_id}-vtr",
        }
    fetch.assert_awaited_once_with(str(staff.project_id), str(staff.id), str(review_id))
    target.assert_awaited_once_with(
        database, staff, session_id, execution_id=review.execution_id,
    )


@pytest.mark.asyncio
async def test_approval_chat_target_uses_current_assignee_and_checked_session(monkeypatch):
    from app.api.v1.endpoints import ai_workflows

    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    approval_id, session_id, visitor_id = uuid4(), uuid4(), uuid4()
    fetch = AsyncMock(return_value=SimpleNamespace(
        visitor_session_id=session_id,
        source_message_id="customer-message-1", source_channel="web",
    ))
    target = AsyncMock(return_value={
        "channel_type": 251, "channel_id": f"{visitor_id}-vtr",
        "message_seq": 42,
    })
    monkeypatch.setattr(workflow_client, "get_approval_customer_session", fetch, raising=False)
    monkeypatch.setattr(ai_workflows, "review_chat_target", target, raising=False)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    database = object()
    app.dependency_overrides[get_db] = lambda: database

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/ai/workflows/approvals/{approval_id}/chat-target",
            params={"visitor_session_id": str(uuid4()), "actor_staff_id": str(uuid4())},
        )
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json() == {
            "channel_type": 251, "channel_id": f"{visitor_id}-vtr",
            "message_seq": 42,
        }
    fetch.assert_awaited_once_with(str(staff.project_id), str(staff.id), str(approval_id))
    target.assert_awaited_once_with(
        database, staff, session_id,
        source_message_id="customer-message-1", source_channel="web",
    )


@pytest.mark.asyncio
async def test_approval_customer_session_client_sends_assignee_and_key(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    approval_id, session_id = str(uuid4()), str(uuid4())
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(
        return_value={
            "visitor_session_id": session_id,
            "source_message_id": "customer-message-1",
            "source_channel": "web",
        },
    ))
    origin = await workflow_client.get_approval_customer_session(
        "project-a", "staff-a", approval_id,
    )
    assert origin.visitor_session_id == UUID(session_id)
    assert origin.source_message_id == "customer-message-1"
    send.assert_awaited_once_with(
        "GET", f"/v1/workflows/approvals/{approval_id}/customer-session",
        params={"project_id": "project-a", "actor_staff_id": "staff-a"},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_aftersales_detail_client_scopes_internal_get(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    review_id = str(uuid4())
    payload = {
        "id": review_id, "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(uuid4()),
        "category": "return_request", "status": "pending_staff_review",
        "review_digest": "a" * 64, "created_at": datetime.now(UTC).isoformat(),
    }
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=payload))
    result = await workflow_client.get_aftersales_review(
        "project-a", "staff-a", review_id,
    )
    assert str(result.visitor_session_id) == payload["visitor_session_id"]
    send.assert_awaited_once_with(
        "GET", f"/v1/workflows/approvals/aftersales-reviews/{review_id}",
        params={"project_id": "project-a", "actor_staff_id": "staff-a"},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_aftersales_intake_proxy_rechecks_trusted_review_and_files(monkeypatch):
    from app.api.v1.endpoints import ai_workflows

    staff = SimpleNamespace(id=uuid4(), project_id=uuid4())
    review_id, session_id, file_id = (uuid4() for _ in range(3))
    preview = WorkflowAfterSalesReviewSummary.model_validate({
        "id": str(review_id), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(session_id),
        "category": "quality_issue", "status": "reviewed",
        "review_result": "needs_manual_service", "reviewed_by": str(staff.id),
        "review_digest": "b" * 64, "created_at": datetime.now(UTC).isoformat(),
    })
    result = WorkflowAfterSalesIntakeSummary.model_validate({
        "review_id": str(review_id), "order_reference": "ORDER-1", "sku": "LV-T01",
        "request_reason": "拉链出现明显损坏", "policy_reference": "售后政策 v1",
        "evidence_file_ids": [str(file_id)], "prepared_by": str(staff.id),
        "prepared_at": datetime.now(UTC).isoformat(),
        "status": "prepared_internal", "external_submission_status": "not_submitted",
    })
    fetch_review = AsyncMock(return_value=preview)
    fetch_intake = AsyncMock(return_value=None)
    prepare = AsyncMock(return_value=result)
    validate_files = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_client, "get_aftersales_review", fetch_review)
    monkeypatch.setattr(workflow_client, "get_aftersales_intake", fetch_intake, raising=False)
    monkeypatch.setattr(workflow_client, "prepare_aftersales_intake", prepare, raising=False)
    monkeypatch.setattr(ai_workflows, "require_customer_read", lambda *_: None)
    monkeypatch.setattr(ai_workflows, "require_intake_evidence_files", validate_files)
    app = FastAPI()
    app.include_router(router, prefix="/v1/ai/workflows")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    database = object()
    app.dependency_overrides[get_db] = lambda: database
    path = f"/v1/ai/workflows/aftersales-reviews/{review_id}/intake"
    body = {
        "review_digest": "b" * 64, "order_reference": "ORDER-1", "sku": "LV-T01",
        "request_reason": "拉链出现明显损坏", "policy_reference": "售后政策 v1",
        "evidence_file_ids": [str(file_id)],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        empty = await client.get(path)
        assert empty.status_code == 200 and empty.json() is None
        assert empty.headers["Cache-Control"] == "no-store"
        assert (await client.post(path, json={**body, "project_id": str(uuid4())})).status_code == 422
        assert (await client.post(
            path, json={**body, "review_digest": "0" * 64},
        )).status_code == 409
        assert validate_files.await_count == 0
        saved = await client.post(path, json=body)
        assert saved.status_code == 200
        assert saved.headers["Cache-Control"] == "no-store"
        assert saved.json()["external_submission_status"] == "not_submitted"
        fetch_intake.return_value = result
        repeated = await client.post(path, json=body)
        assert repeated.status_code == 200
        assert repeated.json()["prepared_at"] == saved.json()["prepared_at"]
        changed = await client.post(path, json={
            **body, "request_reason": "另一个不同的售后诉求",
        })
        assert changed.status_code == 409
    assert fetch_intake.await_count == 4
    validate_files.assert_awaited_once_with(database, staff, session_id, [file_id])
    prepare.assert_awaited_once()
    args = prepare.await_args.args
    assert args[:3] == (str(staff.project_id), str(staff.id), str(review_id))
    assert isinstance(args[3], WorkflowAfterSalesIntakeCreate)


@pytest.mark.asyncio
async def test_aftersales_intake_client_sends_exact_body(monkeypatch):
    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    review_id, file_id = str(uuid4()), uuid4()
    payload = {
        "review_id": review_id, "order_reference": "ORDER-1", "sku": "LV-T01",
        "request_reason": "拉链出现明显损坏", "policy_reference": "售后政策 v1",
        "evidence_file_ids": [str(file_id)], "prepared_by": str(uuid4()),
        "prepared_at": datetime.now(UTC).isoformat(),
        "status": "prepared_internal", "external_submission_status": "not_submitted",
    }
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=payload))
    request = WorkflowAfterSalesIntakeCreate(
        review_digest="a" * 64, order_reference="ORDER-1", sku="LV-T01",
        request_reason="拉链出现明显损坏", policy_reference="售后政策 v1",
        evidence_file_ids=[file_id],
    )
    response = await workflow_client.prepare_aftersales_intake(
        "project-a", "staff-a", review_id, request,
    )
    assert response.review_id == UUID(review_id)
    send.assert_awaited_once_with(
        "POST", f"/v1/workflows/approvals/aftersales-reviews/{review_id}/intake",
        params={"project_id": "project-a", "actor_staff_id": "staff-a"},
        json_data=request.model_dump(mode="json"),
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


@pytest.mark.asyncio
async def test_aftersales_decision_client_sends_exact_body(monkeypatch):
    from app.schemas.workflow_approval import WorkflowAfterSalesReviewDecision

    monkeypatch.setattr("app.services.workflow_client.settings.SECRET_KEY", "test-shared-secret")
    payload = {
        "id": str(uuid4()), "execution_id": str(uuid4()),
        "approval_id": str(uuid4()), "visitor_session_id": str(uuid4()),
        "category": "return_request", "status": "reviewed",
        "review_digest": "a" * 64, "review_result": "needs_manual_service",
        "reviewed_by": str(uuid4()), "reviewed_at": datetime.now(UTC).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
    }
    send = AsyncMock(return_value=object())
    monkeypatch.setattr(workflow_client, "_make_request", send)
    monkeypatch.setattr(workflow_client, "_handle_response", AsyncMock(return_value=payload))
    decision = WorkflowAfterSalesReviewDecision(
        result="needs_manual_service", review_digest="a" * 64,
    )
    response = await workflow_client.decide_aftersales_review(
        "project-a", "staff-a", payload["id"], decision,
    )
    assert str(response.id) == payload["id"]
    send.assert_awaited_once_with(
        "POST", f"/v1/workflows/approvals/aftersales-reviews/{payload['id']}/decision",
        params={"project_id": "project-a", "actor_staff_id": "staff-a"},
        json_data={"result": "needs_manual_service", "review_digest": "a" * 64},
        extra_headers={"X-Internal-API-Key": "test-shared-secret"},
    )


def test_gateway_accepts_the_workflow_approval_node_contract():
    assignee = str(uuid4())
    node = WorkflowNodeInput.model_validate({
        "id": "approval", "type": "approval", "position": {"x": 0, "y": 0},
        "data": {
            "type": "approval", "label": "人工确认", "reference_key": "approval",
            "approver_staff_id": assignee, "summary": "核对只读结果",
            "expires_in_minutes": 60,
            "after_approval_action": "create_aftersales_review",
            "case_category": "return_request",
        },
    })
    assert node.data.type == "approval"
    assert node.model_dump(mode="json")["data"]["approver_staff_id"] == assignee
    assert node.model_dump(mode="json")["data"]["case_category"] == "return_request"

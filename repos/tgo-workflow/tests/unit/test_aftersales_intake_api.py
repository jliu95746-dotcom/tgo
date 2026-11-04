"""An approved review may save one exact internal aftersales intake receipt."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import approvals
from app.database import get_db
from app.main import app
from app.models.aftersales_review import WorkflowAfterSalesReview
from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution


@pytest.mark.asyncio
@pytest.mark.parametrize("with_evidence", [True, False])
async def test_intake_requires_review_exact_preview_and_one_immutable_receipt(
    monkeypatch, with_evidence,
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, execution, approval, review, staff, file_id = (
        str(uuid4()) for _ in range(7)
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(WorkflowAfterSalesReview.__table__.create)
    async with sessions() as db:
        await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow})
        db.add(WorkflowExecution(
            id=execution, project_id=project, workflow_id=workflow, status="completed",
        ))
        db.add(WorkflowApprovalRequest(
            id=approval, project_id=project, execution_id=execution,
            node_id="approval", approver_staff_id=staff,
            summary="核对售后登记", checkpoint={}, status="approved",
            decided_by=staff, decided_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        db.add(WorkflowAfterSalesReview(
            id=review, project_id=project, execution_id=execution,
            approval_id=approval, visitor_session_id=str(uuid4()),
            category="quality_issue", status="reviewed",
            review_result="needs_manual_service", reviewed_by=staff,
            reviewed_at=datetime.now(UTC),
        ))
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_override
    monkeypatch.setattr(approvals.settings, "SECRET_KEY", SecretStr("intake-test-key"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            base = f"/v1/workflows/approvals/aftersales-reviews/{review}"
            params = {"project_id": project, "actor_staff_id": staff}
            headers = {"X-Internal-API-Key": "intake-test-key"}
            preview = await client.get(base, params=params, headers=headers)
            digest = preview.json()["review_digest"]
            intake_url = f"{base}/intake"
            payload = {
                "review_digest": digest,
                "order_reference": "TEST-ORDER-1",
                "sku": "LV-T01",
                "request_reason": "拉链损坏，需要人工核对",
                "policy_reference": "澜月售后政策 v1",
                "evidence_file_ids": [file_id] if with_evidence else [],
            }
            assert (await client.get(intake_url, params=params, headers=headers)).json() is None
            assert (await client.post(
                intake_url, params=params, json=payload,
            )).status_code == 403
            assert (await client.post(
                intake_url,
                params={**params, "actor_staff_id": str(uuid4())},
                headers=headers, json=payload,
            )).status_code == 409
            assert (await client.post(
                intake_url,
                params={**params, "project_id": str(uuid4())},
                headers=headers, json=payload,
            )).status_code == 409
            assert (await client.post(
                intake_url, params=params, headers=headers,
                json={**payload, "review_digest": "0" * 64},
            )).status_code == 409
            assert (await client.post(
                intake_url, params=params, headers=headers,
                json={**payload, "evidence_file_ids": [str(uuid4()) for _ in range(6)]},
            )).status_code == 422
            assert (await client.post(
                intake_url, params=params, headers=headers,
                json={**payload, "order_reference": "   "},
            )).status_code == 422
            async with sessions() as db:
                await db.execute(text(
                    "UPDATE wf_approval_requests SET status = 'rejected' WHERE id = :id"
                ), {"id": approval})
                await db.commit()
            assert (await client.post(
                intake_url, params=params, headers=headers, json=payload,
            )).status_code == 409
            async with sessions() as db:
                await db.execute(text(
                    "UPDATE wf_approval_requests SET status = 'approved' WHERE id = :id"
                ), {"id": approval})
                await db.execute(text(
                    "UPDATE wf_aftersales_reviews SET review_result = 'needs_customer_information' "
                    "WHERE id = :id"
                ), {"id": review})
                await db.commit()
            assert (await client.post(
                intake_url, params=params, headers=headers, json=payload,
            )).status_code == 409
            async with sessions() as db:
                await db.execute(text(
                    "UPDATE wf_aftersales_reviews SET review_result = 'needs_manual_service' "
                    "WHERE id = :id"
                ), {"id": review})
                await db.commit()
            saved = await client.post(intake_url, params=params, headers=headers, json=payload)
            assert saved.status_code == 200
            assert saved.json()["status"] == "prepared_internal"
            assert saved.json()["review_id"] == review
            assert saved.json()["prepared_by"] == staff
            assert saved.json()["evidence_file_ids"] == payload["evidence_file_ids"]
            assert saved.json()["external_submission_status"] == "not_submitted"
            repeated = await client.post(intake_url, params=params, headers=headers, json=payload)
            assert repeated.status_code == 200
            assert repeated.json()["prepared_at"] == saved.json()["prepared_at"]
            assert (await client.post(
                intake_url, params=params, headers=headers,
                json={**payload, "request_reason": "另一件不同的售后诉求"},
            )).status_code == 409
            fetched = await client.get(intake_url, params=params, headers=headers)
            assert fetched.status_code == 200
            assert fetched.headers["Cache-Control"] == "no-store"
            assert fetched.json()["order_reference"] == "TEST-ORDER-1"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

"""The internal review feed must not expose another staff member's tasks."""

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
async def test_review_feed_cursor_keeps_assignee_order_when_new_task_arrives(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, staff = (str(uuid4()) for _ in range(3))
    other_staff = str(uuid4())
    created = datetime(2026, 10, 17, tzinfo=UTC)
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(WorkflowAfterSalesReview.__table__.create)

    async with sessions() as db:
        await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow})
        for status, assignee in (
            ("pending_staff_review", staff), ("pending_staff_review", staff),
            ("pending_staff_review", staff), ("reviewed", staff),
            ("reviewed", staff), ("pending_staff_review", other_staff),
        ):
            execution_id, approval_id, review_id = (str(uuid4()) for _ in range(3))
            db.add(WorkflowExecution(id=execution_id, project_id=project,
                                     workflow_id=workflow, status="completed"))
            db.add(WorkflowApprovalRequest(
                id=approval_id, project_id=project, execution_id=execution_id,
                node_id="approval", approver_staff_id=assignee,
                summary="核对", checkpoint={}, status="approved",
                expires_at=created + timedelta(days=1),
            ))
            db.add(WorkflowAfterSalesReview(
                id=review_id, project_id=project, execution_id=execution_id,
                approval_id=approval_id, visitor_session_id=str(uuid4()),
                category="return_request", status=status, created_at=created,
            ))
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_override
    monkeypatch.setattr(approvals.settings, "SECRET_KEY", SecretStr("review-test-key"))
    try:
        path = "/v1/workflows/approvals/aftersales-reviews"
        params = {"project_id": project, "actor_staff_id": staff, "limit": 2}
        headers = {"X-Internal-API-Key": "review-test-key"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            first = await client.get(path, params=params, headers=headers)
            assert first.status_code == 200, first.text
            assert len(first.json()) == 2
            anchor = first.json()[-1]
            async with sessions() as db:
                execution_id, approval_id, review_id = (str(uuid4()) for _ in range(3))
                db.add(WorkflowExecution(id=execution_id, project_id=project,
                                         workflow_id=workflow, status="completed"))
                db.add(WorkflowApprovalRequest(
                    id=approval_id, project_id=project, execution_id=execution_id,
                    node_id="approval", approver_staff_id=staff, summary="新任务",
                    checkpoint={}, status="approved", expires_at=created + timedelta(days=1),
                ))
                db.add(WorkflowAfterSalesReview(
                    id=review_id, project_id=project, execution_id=execution_id,
                    approval_id=approval_id, visitor_session_id=str(uuid4()),
                    category="return_request", status="pending_staff_review",
                    created_at=created + timedelta(seconds=1),
                ))
                await db.commit()
            cursor = {"before_status": anchor["status"],
                      "before_created_at": anchor["created_at"], "before_id": anchor["id"]}
            second = await client.get(path, params={**params, **cursor}, headers=headers)
            third_anchor = second.json()[-1]
            third = await client.get(path, params={**params,
                "before_status": third_anchor["status"],
                "before_created_at": third_anchor["created_at"],
                "before_id": third_anchor["id"]}, headers=headers)
            half = await client.get(path, params={**params, "before_id": anchor["id"]},
                                    headers=headers)
            foreign = await client.get(path, params={**params, **cursor,
                "project_id": str(uuid4())}, headers=headers)
        rows = first.json() + second.json() + third.json()
        assert second.status_code == 200 and third.status_code == 200
        assert len(rows) == 5
        assert len({row["id"] for row in rows}) == 5
        assert review_id not in {row["id"] for row in rows}
        assert [row["status"] for row in rows] == ["pending_staff_review"] * 3 + ["reviewed"] * 2
        assert [row["id"] for row in rows[:3]] == sorted(
            (row["id"] for row in rows[:3]), reverse=True,
        )
        assert [row["id"] for row in rows[3:]] == sorted(
            (row["id"] for row in rows[3:]), reverse=True,
        )
        assert half.status_code == 400
        assert foreign.json() == []
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_review_feed_requires_private_key_project_and_assignee(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, execution, approval, review, staff = (
        str(uuid4()) for _ in range(6)
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE wf_workflows (id VARCHAR PRIMARY KEY)"))
        await connection.run_sync(WorkflowExecution.__table__.create)
        await connection.run_sync(WorkflowApprovalRequest.__table__.create)
        await connection.run_sync(WorkflowAfterSalesReview.__table__.create)
    async with sessions() as db:
        await db.execute(text("INSERT INTO wf_workflows (id) VALUES (:id)"), {"id": workflow})
        db.add(WorkflowExecution(
            id=execution, project_id=project, workflow_id=workflow,
            status="completed",
        ))
        db.add(WorkflowApprovalRequest(
            id=approval, project_id=project, execution_id=execution,
            node_id="approval", approver_staff_id=staff,
            summary="创建待办", checkpoint={}, status="approved",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        db.add(WorkflowAfterSalesReview(
            id=review, project_id=project, execution_id=execution,
            approval_id=approval, visitor_session_id=str(uuid4()),
            category="return_request", status="pending_staff_review",
        ))
        await db.commit()

    async def session_override():
        async with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session_override
    monkeypatch.setattr(approvals.settings, "SECRET_KEY", SecretStr("review-test-key"))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            path = "/v1/workflows/approvals/aftersales-reviews"
            params = {"project_id": project, "actor_staff_id": staff}
            headers = {"X-Internal-API-Key": "review-test-key"}
            assert (await client.get(path, params=params)).status_code == 403
            assert (await client.get(path, params={**params, "project_id": str(uuid4())},
                                     headers=headers)).json() == []
            assert (await client.get(path, params={**params, "actor_staff_id": str(uuid4())},
                                     headers=headers)).json() == []
            response = await client.get(path, params=params, headers=headers)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()[0]["id"] == review
            assert "checkpoint" not in response.json()[0]
            detail_path = f"{path}/{review}"
            assert (await client.get(detail_path, params=params)).status_code == 403
            assert (await client.get(
                detail_path,
                params={**params, "actor_staff_id": str(uuid4())},
                headers=headers,
            )).status_code == 404
            assert (await client.get(
                detail_path,
                params={**params, "project_id": str(uuid4())},
                headers=headers,
            )).status_code == 404
            detail = await client.get(detail_path, params=params, headers=headers)
            assert detail.status_code == 200
            assert detail.headers["Cache-Control"] == "no-store"
            assert detail.json()["visitor_session_id"] == response.json()[0]["visitor_session_id"]
            assert detail.json()["review_digest"] == response.json()[0]["review_digest"]
            digest = response.json()[0]["review_digest"]
            decision_path = f"{path}/{review}/decision"
            decision = {"result": "needs_manual_service", "review_digest": digest}
            assert (await client.post(
                decision_path, params=params, json=decision,
            )).status_code == 403
            assert (await client.post(
                decision_path,
                params={**params, "actor_staff_id": str(uuid4())},
                headers=headers, json=decision,
            )).status_code == 409
            assert (await client.post(
                decision_path,
                params={**params, "project_id": str(uuid4())},
                headers=headers, json=decision,
            )).status_code == 409
            assert (await client.post(
                decision_path, params=params, headers=headers,
                json={**decision, "result": "refund_payment"},
            )).status_code == 422
            async with sessions() as db:
                row = await db.get(WorkflowAfterSalesReview, review)
                row.category = "quality_issue"
                await db.commit()
            assert (await client.post(
                decision_path, params=params, headers=headers, json=decision,
            )).status_code == 409
            async with sessions() as db:
                row = await db.get(WorkflowAfterSalesReview, review)
                row.category = "return_request"
                await db.commit()
            assert (await client.post(
                decision_path, params=params, headers=headers,
                json={**decision, "review_digest": "0" * 64},
            )).status_code == 409
            decided = await client.post(
                decision_path, params=params, headers=headers, json=decision,
            )
            assert decided.status_code == 200
            assert decided.json()["status"] == "reviewed"
            assert decided.json()["review_result"] == "needs_manual_service"
            assert decided.json()["reviewed_by"] == staff
            assert decided.json()["reviewed_at"] is not None
            repeated = await client.post(
                decision_path, params=params, headers=headers, json=decision,
            )
            assert repeated.status_code == 200
            assert repeated.json()["reviewed_at"] == decided.json()["reviewed_at"]
            changed_result = await client.post(
                decision_path, params=params, headers=headers,
                json={**decision, "result": "not_aftersales"},
            )
            assert changed_result.status_code == 409
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()

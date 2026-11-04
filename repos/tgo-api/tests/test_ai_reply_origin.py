"""An AI badge needs both a saved run and the actual IM stream anchor."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.v1.endpoints import ai_runs
from app.core.database import get_db
from app.core.exceptions import TGOAPIException, tgo_api_exception_handler
from app.core.security import get_current_active_user
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_interaction_run import AIInteractionRun
from app.schemas.ai_runs import AIReplyOriginEvidence
from app.schemas.wukongim import WuKongIMMessage
from app.services.ai_reply_origin import verify_ai_reply_origins
from app.services.im_access import issue_visitor_im_token

pytest_plugins = ("tests.test_im_access",)


def message(client_no: str, *, marker: str = "ai") -> WuKongIMMessage:
    return WuKongIMMessage.model_validate(
        {
            "header": {"no_persist": 0, "red_dot": 0, "sync_once": 0},
            "setting": 2,
            "message_id": 123,
            "client_msg_no": client_no,
            "message_seq": 2,
            "from_uid": f"{uuid4()}-staff",
            "channel_id": "visitor-vtr",
            "channel_type": 251,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "payload": {"type": 100, "content": "answer", "sender_kind": marker},
        }
    )


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    AIInteractionRun.__table__.create(engine)
    AIExecutionAttempt.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.mark.asyncio
async def test_proof_accepts_current_and_retry_only_with_matching_im(db: Session):
    project_id, platform_id, visitor_id = uuid4(), uuid4(), uuid4()
    run = AIInteractionRun(
        project_id=project_id,
        platform_id=platform_id,
        visitor_id=visitor_id,
        channel_id="visitor-vtr",
        channel_type=251,
        source_message_id="source-1",
        request_fingerprint="f" * 64,
        response_client_msg_no="ai_current",
        status="completed",
    )
    db.add(run)
    db.flush()
    db.add(
        AIExecutionAttempt(
            execution_id=uuid4(),
            project_id=project_id,
            interaction_run_id=run.id,
            response_client_msg_no="ai_retry",
            status="failed",
        )
    )
    db.commit()
    lookup = SimpleNamespace(
        enabled=True,
        get_message_by_client_msg_no=AsyncMock(
            side_effect=[
                message("ai_current"),
                message("ai_retry", marker="staff"),
            ]
        ),
    )

    result = await verify_ai_reply_origins(
        db,
        project_id,
        "visitor-vtr",
        ["ai_current", "ai_retry", "ai_unknown"],
        lookup,
    )

    assert [(item.client_msg_no, item.status) for item in result] == [
        ("ai_current", "verified"),
        ("ai_retry", "mismatch"),
        ("ai_unknown", "unrecorded"),
    ]
    assert lookup.get_message_by_client_msg_no.await_count == 2


@pytest.mark.asyncio
async def test_proof_is_project_scoped_and_fails_closed_when_im_unavailable(
    db: Session,
):
    run = AIInteractionRun(
        project_id=uuid4(),
        platform_id=uuid4(),
        visitor_id=uuid4(),
        channel_id="visitor-vtr",
        channel_type=251,
        source_message_id="source-2",
        request_fingerprint="f" * 64,
        response_client_msg_no="ai_other",
        status="completed",
    )
    db.add(run)
    db.commit()
    lookup = SimpleNamespace(
        enabled=False,
        get_message_by_client_msg_no=AsyncMock(),
    )

    foreign = await verify_ai_reply_origins(
        db,
        uuid4(),
        "visitor-vtr",
        ["ai_other"],
        lookup,
    )
    local = await verify_ai_reply_origins(
        db,
        run.project_id,
        "visitor-vtr",
        ["ai_other"],
        lookup,
    )

    assert foreign[0].status == "unrecorded"
    assert local[0].status == "unavailable"
    lookup.get_message_by_client_msg_no.assert_not_awaited()

    lookup.enabled = True
    lookup.get_message_by_client_msg_no.side_effect = RuntimeError("IM unavailable")
    failed_lookup = await verify_ai_reply_origins(
        db,
        run.project_id,
        "visitor-vtr",
        ["ai_other"],
        lookup,
    )
    assert failed_lookup[0].status == "unavailable"


@pytest.mark.asyncio
async def test_staff_endpoint_checks_channel_before_origin_lookup(
    db: Session, monkeypatch
):
    app = FastAPI()
    app.include_router(ai_runs.router, prefix="/v1/ai/runs")
    app.dependency_overrides[get_db] = lambda: db
    actor = SimpleNamespace(project_id=uuid4(), role="user")
    app.dependency_overrides[get_current_active_user] = lambda: actor
    scope = AsyncMock(side_effect=HTTPException(404, "channel not found"))
    verifier = AsyncMock(
        return_value=[
            AIReplyOriginEvidence(
                client_msg_no="ai_one",
                status="verified",
            )
        ]
    )
    monkeypatch.setattr(ai_runs, "require_staff_channel_access", scope)
    monkeypatch.setattr(ai_runs, "verify_ai_reply_origins", verifier)
    body = {"channel_id": "visitor-vtr", "client_msg_nos": ["ai_one"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        denied = await client.post("/v1/ai/runs/reply-origins/verify", json=body)
        assert denied.status_code == 404
        verifier.assert_not_awaited()
        scope.side_effect = None
        accepted = await client.post("/v1/ai/runs/reply-origins/verify", json=body)
        assert accepted.status_code == 200
        assert accepted.headers["cache-control"] == "no-store"
        assert accepted.json() == [{"client_msg_no": "ai_one", "status": "verified"}]
        assert verifier.await_args.args[1] == actor.project_id
        too_many = await client.post(
            "/v1/ai/runs/reply-origins/verify",
            json={
                "channel_id": "visitor-vtr",
                "client_msg_nos": ["ai_one"] * 51,
            },
        )
        assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_visitor_origin_route_requires_current_visitor_and_owned_channel(
    records, monkeypatch
):
    db, _, _, platforms, visitors = records
    app = FastAPI()
    app.include_router(ai_runs.router, prefix="/v1/ai/runs")
    app.add_exception_handler(TGOAPIException, tgo_api_exception_handler)
    app.dependency_overrides[get_db] = lambda: db
    verifier = AsyncMock(
        return_value=[
            AIReplyOriginEvidence(
                client_msg_no="ai_one",
                status="verified",
            )
        ]
    )
    monkeypatch.setattr(ai_runs, "verify_ai_reply_origins", verifier)
    body = {
        "platform_api_key": platforms[0].api_key,
        "channel_id": f"{visitors[0].id}-vtr",
        "client_msg_nos": ["ai_one"],
    }
    headers = {
        "Authorization": "Bearer "
        + issue_visitor_im_token(visitors[0], timedelta(hours=1))
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        no_grant = await client.post(
            "/v1/ai/runs/reply-origins/verify-by-visitor", json=body
        )
        assert no_grant.status_code == 401
        verifier.assert_not_awaited()
        foreign = await client.post(
            "/v1/ai/runs/reply-origins/verify-by-visitor",
            json={**body, "channel_id": f"{visitors[1].id}-vtr"},
            headers=headers,
        )
        assert foreign.status_code == 401
        verifier.assert_not_awaited()
        other_platform = await client.post(
            "/v1/ai/runs/reply-origins/verify-by-visitor",
            json={**body, "platform_api_key": platforms[1].api_key},
            headers=headers,
        )
        assert other_platform.status_code == 401
        verifier.assert_not_awaited()
        allowed = await client.post(
            "/v1/ai/runs/reply-origins/verify-by-visitor",
            json=body,
            headers=headers,
        )
        assert allowed.status_code == 200
        assert allowed.headers["cache-control"] == "no-store"
        assert allowed.json() == [{"client_msg_no": "ai_one", "status": "verified"}]
        assert verifier.await_args.args[1] == platforms[0].project_id

        async def revoke_during_lookup(*args):
            visitors[0].access_revision += 1
            db.commit()
            return verifier.return_value

        verifier.side_effect = revoke_during_lookup
        revoked = await client.post(
            "/v1/ai/runs/reply-origins/verify-by-visitor",
            json=body,
            headers=headers,
        )
        assert revoked.status_code == 401

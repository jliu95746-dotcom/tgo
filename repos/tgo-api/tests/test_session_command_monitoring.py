"""Only a platform operator can inspect stale session command effects."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.api.v1.endpoints import operations_tasks
from app.api.v1.endpoints.operations import (
    require_commercial_operations,
    require_operator,
)
from app.models import SessionCommandDelivery
from app.models.billing import BillingAudit
from app.core.config import settings
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id
from tests.test_staff_session_commands import (  # noqa: F401
    bearer, real_session_api, session_api,
)
from tests.test_staff_visitor_read_access import staff_read_api  # noqa: F401
from tests.test_visitor_profile_access import profile_api  # noqa: F401
from tests.test_im_access import records  # noqa: F401


def make_operation(db, staff, visitor, session, *, status, effects_status, age):
    now = datetime.now(timezone.utc)
    row = SessionCommandDelivery(
        project_id=staff.project_id,
        visitor_id=visitor.id,
        session_id=session.id,
        actor_staff_id=staff.id,
        from_staff_id=staff.id,
        kind="close",
        session_version=session.updated_at,
        actor_role="user",
        actor_token_version=1,
        actor_token_expires_at=now + timedelta(hours=1),
        client_msg_no="monitor-" + uuid4().hex,
        channel_id=build_visitor_channel_id(visitor.id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        from_uid=f"{staff.id}-staff",
        payload={"type": 1001, "content": "private customer text"},
        status=status,
        effects=[],
        effects_status=effects_status,
        updated_at=now - age,
        effects_updated_at=now - age,
        last_error="IM_RECEIPT_UNKNOWN" if status == "unknown" else None,
        effects_last_error=(
            "RECOVERY_FAILED" if effects_status == "unknown" else None
        ),
    )
    db.add(row)
    db.commit()
    return row.id


@pytest.mark.asyncio
async def test_operator_sees_only_stale_redacted_commands(
    real_session_api, monkeypatch,  # noqa: F811
):
    app, db, staff, visitors, _, session = real_session_api
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_SECRET_KEY", SecretStr("test-" * 8))
    app.include_router(operations_tasks.router, prefix="/ops")
    app.dependency_overrides[require_commercial_operations] = lambda: None
    stale_notice = make_operation(
        db, staff[0], visitors[0], session,
        status="unknown", effects_status="done", age=timedelta(minutes=10),
    )
    stale_effect = make_operation(
        db, staff[0], visitors[0], session,
        status="sent", effects_status="unknown", age=timedelta(minutes=10),
    )
    make_operation(
        db, staff[0], visitors[0], session,
        status="pending", effects_status="pending", age=timedelta(seconds=10),
    )
    make_operation(
        db, staff[0], visitors[0], session,
        status="sent", effects_status="done", age=timedelta(minutes=10),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        denied = await client.get(
            "/ops/session-command-alerts", headers=bearer(staff[0]),
        )
        assert denied.status_code in {401, 403}
        app.dependency_overrides[require_operator] = lambda: object()
        allowed = await client.get("/ops/session-command-alerts")
        second_page = await client.get(
            "/ops/session-command-alerts?limit=1&offset=1",
        )
        invalid_window = await client.get(
            "/ops/session-command-alerts?stale_after_seconds=5",
        )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"
    body = allowed.json()
    assert body["total"] == 2
    assert body["stale_after_seconds"] == 300
    assert {item["operation_id"] for item in body["items"]} == {
        str(stale_notice), str(stale_effect),
    }
    assert all(item["project_id"] == str(staff[0].project_id) for item in body["items"])
    assert all(
        datetime.fromisoformat(item["notification_updated_at"].replace("Z", "+00:00")).tzinfo
        for item in body["items"]
    )
    assert "private customer text" not in allowed.text
    assert "visitor_id" not in allowed.text
    assert "payload" not in allowed.text
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 2
    assert second_page.json()["offset"] == 1
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["items"][0]["operation_id"] in {
        str(stale_notice), str(stale_effect),
    }
    assert invalid_window.status_code == 422


@pytest.mark.asyncio
async def test_operator_reconciles_notice_without_resending(
    real_session_api, monkeypatch,  # noqa: F811
):
    from app.services.session_command_delivery import wukongim_client

    app, db, staff, visitors, _, session = real_session_api
    BillingAudit.__table__.create(db.get_bind(), checkfirst=True)
    app.include_router(operations_tasks.router, prefix="/ops")
    app.dependency_overrides[require_commercial_operations] = lambda: None
    monkeypatch.setattr(settings, "OPS_ENABLED", True)
    monkeypatch.setattr(settings, "OPS_SECRET_KEY", SecretStr("test-" * 8))
    operator_id = uuid4()
    operation_id = make_operation(
        db, staff[0], visitors[0], session,
        status="unknown", effects_status="done", age=timedelta(minutes=10),
    )
    operation = db.get(SessionCommandDelivery, operation_id)
    assert operation is not None
    lookup = AsyncMock(return_value=SimpleNamespace(
        from_uid=operation.from_uid,
        payload=operation.payload,
        message_id="existing-im-message",
    ))
    monkeypatch.setattr(wukongim_client, "get_message_by_client_msg_no", lookup)
    request_body = {
        "expected_status": "unknown",
        "reason": "值班人员核对原消息回执",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        denied = await client.post(
            f"/ops/session-command-alerts/{operation_id}/reconcile-notice",
            json=request_body, headers=bearer(staff[0]),
        )
        assert denied.status_code in {401, 403}
        app.dependency_overrides[require_operator] = lambda: SimpleNamespace(
            id=operator_id,
        )
        allowed = await client.post(
            f"/ops/session-command-alerts/{operation_id}/reconcile-notice",
            json=request_body,
        )
        repeated = await client.post(
            f"/ops/session-command-alerts/{operation_id}/reconcile-notice",
            json=request_body,
        )
        recent_id = make_operation(
            db, staff[0], visitors[0], session,
            status="unknown", effects_status="done", age=timedelta(seconds=10),
        )
        recent = await client.post(
            f"/ops/session-command-alerts/{recent_id}/reconcile-notice",
            json=request_body,
        )
        missing = await client.post(
            f"/ops/session-command-alerts/{uuid4()}/reconcile-notice",
            json=request_body,
        )
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"
    assert allowed.json()["notification_status"] == "sent"
    assert repeated.status_code == 409
    assert recent.status_code == 409
    assert missing.status_code == 404
    lookup.assert_awaited_once()
    wukongim_client.send_message.assert_not_awaited()
    audit = db.query(BillingAudit).one()
    assert audit.operator_id == operator_id
    assert audit.project_id == staff[0].project_id
    assert audit.action == "session_notice_reconcile"
    assert audit.detail["operation_id"] == str(operation_id)
    assert audit.detail["result_status"] == "sent"
    assert audit.reason == request_body["reason"]
    assert "private customer text" not in allowed.text

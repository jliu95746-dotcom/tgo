"""Disabled members and failed receipts must not regain privileges or block work."""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.endpoints import company_email
from app.models import Staff
from app.services import ai_usage_recovery, project_registration
from app.services.ai_usage import begin_publication, reserve
from app.services.company_email import utc
from tests.test_ai_usage import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401
from tests.test_company_email_trial import pending_company  # noqa: F401


@pytest.mark.asyncio
async def test_invited_member_cannot_request_company_activation(
    pending_company, monkeypatch
):
    db, staff, account, _ = pending_company
    account.status = "active"
    db.commit()
    enqueue = Mock()
    monkeypatch.setattr(company_email, "require_mail_configuration", lambda: None)
    monkeypatch.setattr(company_email, "limit_registration", AsyncMock())
    monkeypatch.setattr(company_email, "enqueue_action", enqueue)
    await company_email.request_mail(db, staff.username, False)
    enqueue.assert_not_called()
    account.status = "pending"
    db.commit()
    await company_email.request_mail(db, staff.username, False)
    enqueue.assert_called_once_with(db, staff, "verify")


@pytest.mark.asyncio
async def test_channel_reconciliation_excludes_disabled_members(
    pending_company, monkeypatch
):
    db, staff, _, _ = pending_company
    enabled = Staff(
        project_id=staff.project_id,
        username="enabled@example.com",
        password_hash="unused",
        account_enabled=True,
    )
    db.add(enabled)
    db.commit()
    create = AsyncMock()
    monkeypatch.setattr(project_registration.wukongim_client, "create_channel", create)
    await project_registration.ensure_project_staff_channel(db, enabled)
    assert create.call_args.kwargs["subscribers"] == [f"{enabled.id}-staff"]


@pytest.mark.asyncio
async def test_receipt_failure_is_deferred_without_releasing_credit(
    commercial_company, monkeypatch
):
    db, staff, batch = prepare(commercial_company)
    row = reserve(db, staff.project_id, "recovery-failure")
    begin_publication(
        db,
        staff.project_id,
        row.id,
        row.lease_id,
        {
            "kind": "im",
            "channel_id": "synthetic",
            "channel_type": 251,
            "client_msg_no": "synthetic",
        },
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    monkeypatch.setattr(ai_usage_recovery, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(ai_usage_recovery, "release_abandoned", lambda: None)
    monkeypatch.setattr(
        ai_usage_recovery.wukongim_client,
        "get_message_by_client_msg_no",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    )
    await ai_usage_recovery.reconcile_deliveries()
    assert row.status == "review"
    assert utc(row.expires_at) > datetime.now(timezone.utc)
    assert batch.remaining == 0

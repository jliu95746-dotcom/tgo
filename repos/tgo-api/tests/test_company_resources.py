"""Capacity uses trial settings and refuses expired service even outside the UI."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Platform
from app.models.billing import SubscriptionPeriod
from app.services.company_resources import resources, reserve_channel_creation
from tests.test_billing_quotes import commercial_company  # noqa: F401


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kwargs):
    return "JSON"


def test_trial_capacity_and_channel_limit(commercial_company, monkeypatch):
    db, staff, _, account, _ = commercial_company
    account.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    Platform.__table__.create(db.get_bind())
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_TRIAL_CHANNELS", 1)
    assert resources(db, staff.project_id).knowledge_bytes == settings.SAAS_TRIAL_KNOWLEDGE_BYTES
    reserve_channel_creation(db, staff.project_id)
    db.add(Platform(project_id=staff.project_id, name="synthetic website", type="website", api_key="synthetic"))
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        reserve_channel_creation(db, staff.project_id)
    assert error.value.code == "CHANNEL_LIMIT_EXCEEDED"


def test_expired_company_cannot_authorize_knowledge_tasks(commercial_company):
    db, staff, _, account, _ = commercial_company
    account.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(TGOAPIException) as error:
        resources(db, staff.project_id)
    assert error.value.code == "SUBSCRIPTION_EXPIRED"


def test_unlimited_paid_channels_keep_legacy_snapshot_compatible(commercial_company, monkeypatch):
    db, staff, plan, account, _ = commercial_company
    now = datetime.now(timezone.utc)
    account.status = "active"
    account.plan_id = plan.id
    account.expires_at = now + timedelta(days=1)
    period = SubscriptionPeriod(
        project_id=staff.project_id, order_id=uuid4(), plan_id=plan.id,
        starts_at=now - timedelta(days=29), ends_at=account.expires_at,
        months=1, anchor_day=now.day, original_price=3000, current_price=3000,
        original_definition=plan.definition, definition=plan.definition,
    )
    Platform.__table__.create(db.get_bind())
    db.add(period)
    for index in range(plan.definition["channel_limit"]):
        db.add(Platform(project_id=staff.project_id, name=f"synthetic website {index}",
                        type="website", api_key=f"synthetic-{index}"))
    db.commit()
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    with pytest.raises(TGOAPIException) as error:
        reserve_channel_creation(db, staff.project_id)
    assert error.value.code == "CHANNEL_LIMIT_EXCEEDED"

    period.definition = {**period.definition, "channel_limit": None}
    db.commit()
    assert resources(db, staff.project_id).channel_limit is None
    reserve_channel_creation(db, staff.project_id)

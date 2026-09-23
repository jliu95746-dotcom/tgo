"""Capacity uses trial settings and refuses expired service even outside the UI."""

from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Platform
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

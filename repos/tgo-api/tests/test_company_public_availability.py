"""Public service status discloses availability only, with server-side expiry."""

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.services.company_entitlements import new_service_available
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.mark.parametrize(
    "state,days,expected",
    [
        ("trial", 1, True),
        ("active", -1, False),
        ("suspended", 1, False),
        ("pending", 1, False),
    ],
)
def test_service_availability_uses_server_time(
    commercial_company, monkeypatch, state, days, expected
):
    db, staff, _, account, _ = commercial_company
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    account.status = state
    account.expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    db.commit()
    assert new_service_available(db, staff.project_id) is expected


def test_billing_disabled_does_not_pause_legacy_service(
    commercial_company, monkeypatch
):
    db, staff, _, account, _ = commercial_company
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    account.status = "pending"
    assert new_service_available(db, staff.project_id)

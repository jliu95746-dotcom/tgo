"""Reception staff cannot modify company AI configuration by calling APIs."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import company_configuration_access as access
from app.core.config import settings
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.mark.parametrize(
    "role,method,allowed",
    [
        ("user", "POST", False),
        ("user", "PATCH", False),
        ("admin", "POST", True),
        ("user", "GET", True),
    ],
)
def test_configuration_write_requires_company_admin(
    commercial_company, monkeypatch, role, method, allowed
):
    db, staff, _, account, _ = commercial_company
    staff.role = role
    account.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(access, "resolve_staff_token", lambda *args: staff)
    request = Request(
        {
            "type": "http",
            "method": method,
            "path": "/v1/ai/agents",
            "headers": [(b"authorization", b"Bearer synthetic")],
        }
    )
    if allowed:
        access.require_configuration_write(request, db)
    else:
        with pytest.raises(HTTPException) as error:
            access.require_configuration_write(request, db)
        assert error.value.status_code == 403


def test_rollout_disabled_does_not_query_new_tables(monkeypatch):
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    db = Mock()
    access.require_configuration_write(
        Request({"type": "http", "method": "POST", "headers": []}), db
    )
    db.get.assert_not_called()

"""Administrative rejections must not disclose staff identities in logs."""

from unittest.mock import Mock

from fastapi import HTTPException
import pytest

from app.core import security
from app.models.staff import Staff


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["user", "agent"])
async def test_admin_rejection_omits_username(monkeypatch, role):
    private_username = "synthetic-private-staff@example.invalid"
    staff = Staff(username=private_username, role=role)
    warning = Mock()
    monkeypatch.setattr(security.logger, "warning", warning)
    with pytest.raises(HTTPException) as error:
        await security.require_admin()(staff)
    assert error.value.status_code == 403
    assert error.value.detail == "Admin permission required"
    assert warning.call_count == 1
    assert private_username not in repr(warning.call_args)


@pytest.mark.asyncio
async def test_admin_dependency_keeps_the_authenticated_staff(monkeypatch):
    staff = Staff(username="synthetic-admin@example.invalid", role="admin")
    warning = Mock()
    monkeypatch.setattr(security.logger, "warning", warning)
    assert await security.require_admin()(staff) is staff
    warning.assert_not_called()

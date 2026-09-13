"""Session status and signed lifecycle access are explicit, not UI guesses."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.device_session_auth import require_session_writer
from app.schemas.device_access import DeviceServicePrincipal
from app.services.device_sessions import effective_session_status


@pytest.mark.parametrize(
    "stored,expired,expected",
    [
        ("running", False, "running"),
        ("running", True, "interrupted"),
        ("completed", True, "completed"),
        ("failed", True, "failed"),
        ("cancelled", True, "cancelled"),
        ("interrupted", False, "interrupted"),
    ],
)
def test_expired_execution_never_looks_running_or_successful(
    stored, expired, expected
):
    now = datetime.now(timezone.utc)
    session = SimpleNamespace(
        status=stored,
        lease_expires_at=now + timedelta(seconds=-1 if expired else 60),
    )
    assert effective_session_status(session, now) == expected
    assert session.status == stored  # Listing must not mutate a stored result.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["api", "other-device", "other-session", "missing-session", "valid"],
)
async def test_only_the_signed_execution_can_write_its_lifecycle(case):
    project, device, session = uuid4(), uuid4(), uuid4()
    principal = DeviceServicePrincipal(
        sub="tgo-api" if case == "api" else "tgo-ai",
        project_id=project,
        device_id=uuid4() if case == "other-device" else device,
        session_id=None
        if case == "missing-session"
        else uuid4()
        if case == "other-session"
        else session,
    )
    if case == "valid":
        assert (
            await require_session_writer(device, session, principal)
            == principal
        )
    else:
        with pytest.raises(HTTPException) as error:
            await require_session_writer(device, session, principal)
        assert error.value.status_code == 403

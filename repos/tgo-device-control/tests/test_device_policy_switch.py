"""A file-to-screen switch retires a version; it never deletes or revives scope."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.device_read_policy import DeviceReadPolicy
from app.schemas.device_read_policy import DeviceReadPolicyRevoke, DeviceReadPolicyTest
from app.services import device_read_policy as reads
from app.services import device_screen_publication as screens
from app.services.device_read_policy import DeviceReadPolicyService, read_policy_digest
from test_screen_publication import connection, database, request, row


def read_policy(project, device, *, enabled=True):
    return DeviceReadPolicy(
        device_id=device,
        project_id=project,
        enabled=enabled,
        revision=1,
        allowed_directory="/srv/fixture",
        max_lines=10,
        max_offset=10,
        digest=read_policy_digest(1, "/srv/fixture", 10, 10),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_file_revoke_keeps_version_and_clears_test_and_exact_connection(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = read_policy(project, device)
    saved.last_test_status, saved.last_test_digest = "passed", saved.digest
    conn = connection(project, device)
    conn.desired_read_policy_digest = saved.digest
    conn.read_policy_verified_digest = saved.digest
    db = database(device, saved)
    close = AsyncMock()
    monkeypatch.setattr(reads.tcp_connection_manager, "get_connection", lambda _: conn)
    monkeypatch.setattr(reads.tcp_connection_manager, "unregister_connection", close)
    result = await DeviceReadPolicyService(db).revoke(
        project, device, DeviceReadPolicyRevoke(expected_policy_revision=1)
    )
    assert result.status == "revoked" and result.revision == 2 and not result.verified
    assert saved.enabled is False and saved.revoked_at is not None
    assert saved.digest == read_policy_digest(2, "/srv/fixture", 10, 10)
    assert saved.last_test_digest is None and conn.read_policy_verified_digest is None
    close.assert_awaited_once_with(str(device), expected_connection=conn)


@pytest.mark.asyncio
async def test_revoked_read_is_not_reapplied_or_tested(monkeypatch):
    project, device = uuid4(), uuid4()
    saved = read_policy(project, device, enabled=False)
    conn = connection(project, device)
    reads.set_desired_policy(conn, saved)
    assert not await reads.apply_read_policy(conn, saved)
    conn.send_request.assert_not_awaited()
    db = database(device, saved)
    monkeypatch.setattr(reads.tcp_connection_manager, "get_connection", lambda _: conn)
    with pytest.raises(HTTPException) as error:
        await DeviceReadPolicyService(db).test_read(
            project,
            device,
            DeviceReadPolicyTest(path="/srv/fixture/proof.txt", expected_text="marker"),
        )
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_revoked_file_marker_can_only_be_replaced_by_fresh_local_screen_scope(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = read_policy(project, device, enabled=False)
    conn = connection(project, device)
    conn.desired_read_policy_digest = saved.digest
    db = database(device, None, saved, None, device, row(project, device))
    monkeypatch.setattr(
        screens.tcp_connection_manager, "get_connection", lambda _: conn
    )
    apply = AsyncMock(return_value=False)
    monkeypatch.setattr(screens, "apply_screen_policy", apply)
    result = await screens.DeviceScreenPublicationService(db).publish(
        project, device, request()
    )
    assert result.status == "pending" and result.verified is False
    assert conn.desired_read_policy_digest is None
    apply.assert_awaited_once()
    assert saved.enabled is False  # Retired file history was not deleted.


@pytest.mark.asyncio
async def test_stale_file_revoke_and_foreign_owner_cannot_close_device(monkeypatch):
    project, device = uuid4(), uuid4()
    saved = read_policy(project, device)
    db = database(device, saved, None)
    close = AsyncMock()
    monkeypatch.setattr(reads.tcp_connection_manager, "unregister_connection", close)
    service = DeviceReadPolicyService(db)
    for owner, revision in ((project, 2), (uuid4(), 1)):
        with pytest.raises(HTTPException) as error:
            await service.revoke(
                owner, device, DeviceReadPolicyRevoke(expected_policy_revision=revision)
            )
        assert error.value.status_code in {404, 409}
    close.assert_not_awaited()
    db.commit.assert_not_awaited()

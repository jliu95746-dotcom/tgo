"""Published screen scope is tenant-owned, versioned and never a business pass."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.device_screen_policy import DeviceScreenPolicy
from app.schemas.device_screen_publication import (
    ScreenPolicyPublish,
    ScreenPolicyRevoke,
)
from app.services import device_screen_publication as publication
from app.services.device_screen_publication import DeviceScreenPublicationService
from test_screen_policy_contract import grant, policy


def request(**changes):
    values = dict(
        expected_policy_revision=0,
        connection_instance_id="c" * 32,
        grant_id="a" * 32,
        max_actions=5,
        allow_click=False,
        allow_text=False,
        click_regions=[],
    )
    values.update(changes)
    return ScreenPolicyPublish.model_validate(values)


def connection(project_id, device_id):
    return SimpleNamespace(
        agent_id=str(device_id),
        project_id=str(project_id),
        connection_instance_id="c" * 32,
        desired_read_policy_digest=None,
        desired_screen_policy=None,
        applied_screen_policy_digest=None,
        screen_grant=None,
        send_request=AsyncMock(return_value=grant().model_dump()),
    )


def row(project_id, device_id, revision=1, enabled=True):
    scope = policy(revision=revision)
    return DeviceScreenPolicy(
        device_id=device_id,
        project_id=project_id,
        revision=revision,
        enabled=enabled,
        connection_instance_id="c" * 32,
        grant_id=scope.grant_id,
        max_actions=scope.max_actions,
        allow_click=scope.allow_click,
        allow_text=scope.allow_text,
        click_regions=[],
        digest=scope.digest,
        updated_at=datetime.now(timezone.utc),
    )


def database(*values):
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=values)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    return db


def test_publish_requires_explicit_bounded_scope_and_connection():
    for changes in (
        {"connection_instance_id": ""},
        {"grant_id": ""},
        {"max_actions": True},
        {"allow_text": None},
        {"window_handle": 10},
        {"expected_policy_revision": -1},
    ):
        with pytest.raises(ValidationError):
            request(**changes)


@pytest.mark.asyncio
async def test_foreign_device_cannot_query_publish_or_revoke():
    db = database(None, None, None)
    service = DeviceScreenPublicationService(db)
    for operation in (
        service.local_grant(uuid4(), uuid4()),
        service.publish(uuid4(), uuid4(), request()),
        service.revoke(
            uuid4(), uuid4(), ScreenPolicyRevoke(expected_policy_revision=1)
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await operation
        assert error.value.status_code == 404
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_version_cannot_replace_scope(monkeypatch):
    project, device = uuid4(), uuid4()
    existing = row(project, device, revision=3)
    db = database(device, existing)
    conn = connection(project, device)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    with pytest.raises(HTTPException) as error:
        await DeviceScreenPublicationService(db).publish(project, device, request())
    assert error.value.status_code == 409
    assert existing.revision == 3
    conn.send_request.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["connection", "grant", "read-policy", "scope"])
async def test_changed_local_authority_cannot_be_persisted(monkeypatch, changed):
    project, device = uuid4(), uuid4()
    conn = connection(project, device)
    if changed == "connection":
        conn.connection_instance_id = "d" * 32
    elif changed == "grant":
        conn.send_request.return_value = (
            grant().model_copy(update={"grant_id": "b" * 32}).model_dump()
        )
    elif changed == "read-policy":
        conn.desired_read_policy_digest = "e" * 64
    values = [device, None, object() if changed == "read-policy" else None, None]
    db = database(*values)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    with pytest.raises(HTTPException) as error:
        await DeviceScreenPublicationService(db).publish(
            project, device, request(max_actions=11 if changed == "scope" else 5)
        )
    assert error.value.status_code == 409 or error.value.status_code == 422
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_acknowledgement_is_persisted_scope_but_never_business_verified(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, None, None, None, device, saved)
    conn = connection(project, device)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )

    async def apply(current, scope):
        assert db.commit.await_count == 1
        assert scope == policy()
        current.desired_screen_policy = scope
        current.applied_screen_policy_digest = scope.digest
        current.screen_grant = grant()
        return True

    monkeypatch.setattr(publication, "apply_screen_policy", apply)
    response = await DeviceScreenPublicationService(db).publish(
        project, device, request()
    )
    assert response.status == "active"
    assert response.verified is False
    assert response.policy.digest == policy().digest
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_revoke_retains_version_tombstone_and_closes_only_bound_connection(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, saved)
    conn = connection(project, device)
    conn.desired_screen_policy = policy()
    conn.applied_screen_policy_digest = policy().digest
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    result = await DeviceScreenPublicationService(db).revoke(
        project, device, ScreenPolicyRevoke(expected_policy_revision=1)
    )
    assert result.status == "revoked" and result.policy.revision == 2
    assert result.policy.digest == policy(revision=2).digest
    assert not saved.enabled and saved.revoked_at is not None
    assert conn.applied_screen_policy_digest is None
    close.assert_awaited_once_with(str(device), expected_connection=conn)
    assert db.commit.await_count == 1


@pytest.mark.asyncio
async def test_restart_and_replacement_do_not_reapply_persisted_local_grant(
    monkeypatch,
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, saved)
    conn = connection(project, device)
    conn.connection_instance_id = "d" * 32
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    result = await DeviceScreenPublicationService(db).get(project, device)
    assert result.status == "pending" and not result.verified
    conn.send_request.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", ["stopped", "reduced-budget"])
async def test_stopped_or_reduced_local_scope_clears_cached_status(
    monkeypatch, unavailable
):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, saved)
    conn = connection(project, device)
    conn.desired_screen_policy = policy()
    conn.applied_screen_policy_digest = policy().digest
    conn.screen_grant = grant()
    conn.send_request.return_value = (
        {"protocol_version": 1, "active": False}
        if unavailable == "stopped"
        else grant().model_copy(update={"max_actions": 4}).model_dump()
    )
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    result = await DeviceScreenPublicationService(db).get(project, device)
    assert result.status == "pending" and not result.verified
    assert conn.applied_screen_policy_digest is None


@pytest.mark.asyncio
async def test_failed_acknowledgement_leaves_persisted_policy_pending(monkeypatch):
    project, device = uuid4(), uuid4()
    saved = row(project, device)
    db = database(device, None, None, None, device, saved)
    conn = connection(project, device)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    monkeypatch.setattr(
        publication, "apply_screen_policy", AsyncMock(return_value=False)
    )
    result = await DeviceScreenPublicationService(db).publish(
        project, device, request()
    )
    assert result.status == "pending" and result.verified is False
    assert db.commit.await_count == 2


@pytest.mark.asyncio
async def test_commit_failure_after_ack_closes_exact_connection(monkeypatch):
    project, device = uuid4(), uuid4()
    db = database(device, None, None, None, device, row(project, device))
    db.commit.side_effect = [None, RuntimeError("owned commit failure")]
    conn = connection(project, device)
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )

    async def apply(current, scope):
        current.desired_screen_policy = scope
        current.applied_screen_policy_digest = scope.digest
        return True

    monkeypatch.setattr(publication, "apply_screen_policy", apply)
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    with pytest.raises(RuntimeError, match="owned commit failure"):
        await DeviceScreenPublicationService(db).publish(project, device, request())
    assert conn.applied_screen_policy_digest is None
    close.assert_awaited_once_with(str(device), expected_connection=conn)


@pytest.mark.asyncio
async def test_revoke_commit_failure_still_closes_old_local_grant(monkeypatch):
    project, device = uuid4(), uuid4()
    db = database(device, row(project, device))
    db.commit.side_effect = RuntimeError("owned revoke commit failure")
    conn = connection(project, device)
    conn.desired_screen_policy = policy()
    conn.applied_screen_policy_digest = policy().digest
    monkeypatch.setattr(
        publication.tcp_connection_manager, "get_connection", lambda _: conn
    )
    close = AsyncMock()
    monkeypatch.setattr(
        publication.tcp_connection_manager, "unregister_connection", close
    )
    with pytest.raises(RuntimeError, match="owned revoke commit failure"):
        await DeviceScreenPublicationService(db).revoke(
            project, device, ScreenPolicyRevoke(expected_policy_revision=1)
        )
    assert conn.applied_screen_policy_digest is None
    close.assert_awaited_once_with(str(device), expected_connection=conn)

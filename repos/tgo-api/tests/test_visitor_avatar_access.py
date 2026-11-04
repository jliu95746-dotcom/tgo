"""Avatar bytes require current authority and the exact saved image."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import update

from app.api.v1.endpoints import visitors as endpoints
from app.core.exceptions import TGOAPIException
from app.models import Visitor
from app.schemas.staff_data_access import StaffDataAccess
from app.services.profile_access import require_profile_access
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api


@pytest.fixture
def avatar_api(profile_api, tmp_path, monkeypatch):
    app, db, _, staff, platforms, visitors, _, _, headers, _ = profile_api
    monkeypatch.setattr(endpoints.settings, "UPLOAD_BASE_DIR", str(tmp_path))
    visitor = visitors[0]
    directory = tmp_path / "avatars" / str(visitor.project_id) / str(visitor.id)
    directory.mkdir(parents=True)
    image = directory / "saved.png"
    image.write_bytes(b"saved-test-image")
    visitor.avatar_url = f"/v1/visitors/{visitor.id}/avatar?v=saved.png"
    db.commit()
    yield app, db, staff, visitors, platforms, headers, image


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["anonymous", "key", "other", "foreign_staff"])
async def test_avatar_denies_unproven_or_foreign_actor(avatar_api, actor):
    from app.core.security import create_access_token
    from app.services.im_access import issue_visitor_im_token

    app, _, staff, visitors, platforms, headers, _ = avatar_api
    proof = {}
    if actor == "key":
        proof = {"X-Platform-API-Key": platforms[0].api_key}
    if actor == "other":
        proof = {**headers, "Authorization": "Bearer " + issue_visitor_im_token(visitors[1], timedelta(hours=1))}
    if actor == "foreign_staff":
        proof = {"Authorization": "Bearer " + create_access_token(staff[1].username, staff[1].project_id, staff[1].role)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar", headers=proof)
    assert response.status_code in (401, 404)
    assert b"saved-test-image" not in response.content


@pytest.mark.asyncio
async def test_avatar_reads_exact_version_with_no_store(avatar_api):
    app, _, _, visitors, _, headers, image = avatar_api
    (image.parent / "newer.png").write_bytes(b"uncommitted-image")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar?v=saved.png", headers=headers)
        stale = await client.get(f"/visitors/{visitors[0].id}/avatar?v=other.png", headers=headers)
    assert response.status_code == 200
    assert response.content == b"saved-test-image"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert stale.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["ended", "rotated", "disabled", "removed"])
async def test_avatar_checks_current_visitor_authority(avatar_api, change):
    app, db, _, visitors, platforms, headers, _ = avatar_api
    if change == "ended":
        visitors[0].access_retired_at = datetime.now(timezone.utc)
    if change == "rotated":
        platforms[0].api_key = "rotated-test-key"
    if change == "disabled":
        platforms[0].is_active = False
    if change == "removed":
        visitors[0].deleted_at = datetime.now(timezone.utc)
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)
    assert response.status_code in (401, 404)


@pytest.mark.asyncio
@pytest.mark.parametrize("saved", ["?v=../saved.png", "?v=%2e%2e%2fsaved.png", "?v=missing.png", "?v=saved.png&v=newer.png", "?v=saved.png&extra=1"])
async def test_avatar_rejects_invalid_saved_path(avatar_api, saved):
    app, db, _, visitors, _, headers, _ = avatar_api
    visitors[0].avatar_url = f"/v1/visitors/{visitors[0].id}/avatar{saved}"
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_legacy_avatar_requires_one_unambiguous_image(avatar_api):
    app, db, _, visitors, _, headers, image = avatar_api
    visitors[0].avatar_url = f"/v1/visitors/{visitors[0].id}/avatar"
    db.commit()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)).content == b"saved-test-image"
        (image.parent / "other.png").write_bytes(b"not-current")
        assert (await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_upload_rechecks_authority_after_read_and_cleans_only_new_object(avatar_api, monkeypatch):
    from app.core.security import create_access_token
    from app.services import visitor_avatar_access

    _, db, staff, visitors, _, _, image = avatar_api
    token = create_access_token(staff[0].username, staff[0].project_id, staff[0].role)
    access = require_profile_access(db, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), None)
    assert isinstance(access, StaffDataAccess)
    old_url = visitors[0].avatar_url

    async def revoked_read(_size):
        staff[0].account_enabled = False
        db.commit()
        return b"new-image"

    file = UploadFile(filename="new.png", file=None, headers={"content-type": "image/png"})
    monkeypatch.setattr(file, "read", AsyncMock(side_effect=revoked_read))
    with pytest.raises(TGOAPIException):
        await visitor_avatar_access.save_avatar(db, access, visitors[0].id, file)
    assert image.read_bytes() == b"saved-test-image"
    assert list(image.parent.iterdir()) == [image]
    assert visitors[0].avatar_url == old_url


@pytest.mark.asyncio
async def test_avatar_response_checks_each_body_chunk(avatar_api):
    from app.services.visitor_avatar_access import avatar_response

    _, db, _, visitors, _, headers, image = avatar_api
    access = require_profile_access(db, HTTPAuthorizationCredentials(scheme="Bearer", credentials=headers["Authorization"][7:]), headers["X-Platform-API-Key"])
    response = avatar_response(db, access, visitors[0].id, None)
    response.response.chunk_size = 4
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            visitors[0].access_revision += 1
            db.commit()

    with pytest.raises(TGOAPIException):
        await response({"type": "http", "method": "GET", "headers": [], "extensions": {"http.response.pathsend": {}}}, AsyncMock(), send)
    bodies = [item for item in sent if item["type"] == "http.response.body"]
    assert len(bodies) == 1 and bodies[0]["body"] == image.read_bytes()[:4]


@pytest.mark.asyncio
async def test_end_only_grant_cannot_read_avatar(avatar_api):
    from app.services.visitor_end_grant import issue_visitor_end_token

    app, db, _, visitors, _, headers, _ = avatar_api
    visitors[0].access_secret_digest = "a" * 64
    db.commit()
    proof = {**headers, "Authorization": "Bearer " + issue_visitor_end_token(visitors[0]).get_secret_value()}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar", headers=proof)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_staff_assignment_transfer_revokes_avatar(avatar_api, monkeypatch):
    from app.core.config import settings
    from app.core.security import create_access_token
    from app.models import VisitorSession

    app, db, staff, visitors, _, _, _ = avatar_api
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    staff[0].role = "user"
    db.commit()
    headers = {"Authorization": "Bearer " + create_access_token(staff[0].username, staff[0].project_id, "user")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)
        assert response.status_code == 200
        session = db.query(VisitorSession).filter(VisitorSession.visitor_id == visitors[0].id).one()
        session.staff_id = None
        db.commit()
        assert (await client.get(f"/visitors/{visitors[0].id}/avatar", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_upload_cancellation_keeps_bound_image(avatar_api, monkeypatch):
    from asyncio import CancelledError
    from app.core.security import create_access_token
    from app.services.visitor_avatar_access import save_avatar

    _, db, staff, visitors, _, _, image = avatar_api
    token = create_access_token(staff[0].username, staff[0].project_id, staff[0].role)
    access = require_profile_access(db, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), None)
    assert isinstance(access, StaffDataAccess)
    file = UploadFile(filename="new.png", file=None, headers={"content-type": "image/png"})
    monkeypatch.setattr(file, "read", AsyncMock(side_effect=CancelledError))
    with pytest.raises(CancelledError):
        await save_avatar(db, access, visitors[0].id, file)
    assert list(image.parent.iterdir()) == [image]
    assert image.read_bytes() == b"saved-test-image"


def test_avatar_version_commit_rejects_stale_writer(avatar_api):
    from fastapi import HTTPException
    from app.services.visitor_avatar_access import commit_avatar_version

    _, db, _, visitors, _, _, image = avatar_api
    visitor = visitors[0]
    original_url = visitor.avatar_url
    newer_url = f"/v1/visitors/{visitor.id}/avatar?v=newer.png"
    stale_url = f"/v1/visitors/{visitor.id}/avatar?v=stale.png"
    stale = image.parent / "stale.png"
    stale.write_bytes(b"stale-image")
    db.execute(
        update(Visitor).where(Visitor.id == visitor.id)
        .values(avatar_url=newer_url).execution_options(synchronize_session=False)
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        commit_avatar_version(db, visitor, original_url, visitor.access_revision, stale_url, stale)
    assert error.value.status_code == 409
    db.refresh(visitor)
    assert visitor.avatar_url == newer_url


def test_avatar_version_commit_rejects_changed_access_revision(avatar_api):
    from fastapi import HTTPException
    from app.services.visitor_avatar_access import commit_avatar_version

    _, db, _, visitors, _, _, image = avatar_api
    visitor = visitors[0]
    original_url = visitor.avatar_url
    original_revision = visitor.access_revision
    stale = image.parent / "stale.png"
    stale.write_bytes(b"stale-image")
    db.execute(
        update(Visitor).where(Visitor.id == visitor.id)
        .values(access_revision=original_revision + 1)
        .execution_options(synchronize_session=False)
    )
    db.commit()

    with pytest.raises(HTTPException) as error:
        commit_avatar_version(
            db, visitor, original_url, original_revision,
            f"/v1/visitors/{visitor.id}/avatar?v=stale.png",
            stale,
        )
    assert error.value.status_code == 409
    db.refresh(visitor)
    assert visitor.avatar_url == original_url


def test_avatar_version_commit_rejects_candidate_removed_before_lock(avatar_api):
    from fastapi import HTTPException
    from app.services.visitor_avatar_access import commit_avatar_version

    _, db, _, visitors, _, _, image = avatar_api
    visitor = visitors[0]
    original_url = visitor.avatar_url
    missing = image.parent / "removed.png"

    with pytest.raises(HTTPException) as error:
        commit_avatar_version(
            db, visitor, original_url, visitor.access_revision,
            f"/v1/visitors/{visitor.id}/avatar?v={missing.name}", missing,
        )
    assert error.value.status_code == 409
    db.refresh(visitor)
    assert visitor.avatar_url == original_url


@pytest.mark.asyncio
async def test_avatar_version_conflict_cleans_only_candidate(avatar_api, monkeypatch):
    from io import BytesIO
    from fastapi import HTTPException
    from app.core.security import create_access_token
    from app.services import visitor_avatar_access

    _, db, staff, visitors, _, _, image = avatar_api
    token = create_access_token(staff[0].username, staff[0].project_id, staff[0].role)
    access = require_profile_access(db, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), None)
    original_url = visitors[0].avatar_url
    winner = image.parent / "winner.png"
    winner_url = f"/v1/visitors/{visitors[0].id}/avatar?v=winner.png"
    actual_commit = visitor_avatar_access.commit_avatar_version

    def interleaved_commit(current_db, visitor, old_url, old_revision, new_url, destination):
        winner.write_bytes(b"winning-image")
        current_db.execute(
            update(Visitor).where(Visitor.id == visitor.id)
            .values(avatar_url=winner_url).execution_options(synchronize_session=False)
        )
        current_db.commit()
        actual_commit(current_db, visitor, old_url, old_revision, new_url, destination)

    monkeypatch.setattr(visitor_avatar_access, "commit_avatar_version", interleaved_commit)
    file = UploadFile(filename="new.png", file=BytesIO(b"candidate"), headers={"content-type": "image/png"})
    with pytest.raises(HTTPException) as error:
        await visitor_avatar_access.save_avatar(db, access, visitors[0].id, file)
    assert error.value.status_code == 409
    db.refresh(visitors[0])
    assert visitors[0].avatar_url == winner_url != original_url
    assert set(image.parent.iterdir()) == {image, winner}
    assert image.read_bytes() == b"saved-test-image"
    assert winner.read_bytes() == b"winning-image"


@pytest.mark.asyncio
async def test_avatar_replacement_stops_in_progress_old_version(avatar_api):
    from app.services.visitor_avatar_access import avatar_response

    _, db, _, visitors, _, headers, image = avatar_api
    access = require_profile_access(db, HTTPAuthorizationCredentials(scheme="Bearer", credentials=headers["Authorization"][7:]), headers["X-Platform-API-Key"])
    response = avatar_response(db, access, visitors[0].id, "saved.png")
    response.response.chunk_size = 4
    bodies = []

    async def send(message):
        if message["type"] == "http.response.body":
            bodies.append(message["body"])
            replacement = image.parent / "replacement.png"
            replacement.write_bytes(b"replacement")
            visitors[0].avatar_url = f"/v1/visitors/{visitors[0].id}/avatar?v=replacement.png"
            db.commit()

    with pytest.raises(TGOAPIException):
        await response({"type": "http", "method": "GET", "headers": []}, AsyncMock(), send)
    assert bodies == [b"save"]

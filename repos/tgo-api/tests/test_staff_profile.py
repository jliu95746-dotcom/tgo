"""Self-service profile edits must not grant member-management privileges."""

import base64
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.v1.endpoints import staff
from app.core import security
from app.core.database import get_db
from app.models import Project, Staff
from app.services import staff_profile_service

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+jBz8AAAAASUVORK5CYII="
)


@pytest.fixture
def profile_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Project.__table__.create(engine)
    Staff.__table__.create(engine)
    monkeypatch.setattr(
        staff_profile_service.settings, "UPLOAD_BASE_DIR", str(tmp_path)
    )
    with Session(engine) as db:
        projects = [
            Project(name=name, api_key=f"fixture-{name}") for name in ["a", "b"]
        ]
        db.add_all(projects)
        db.flush()
        own = Staff(
            project_id=projects[0].id,
            username="own@example.test",
            nickname="旧昵称",
            password_hash="fixture",
            role="user",
        )
        other = Staff(
            project_id=projects[1].id,
            username="other@example.test",
            nickname="其他企业",
            password_hash="fixture",
            role="admin",
        )
        db.add_all([own, other])
        db.commit()
        app = FastAPI()
        app.include_router(staff.router, prefix="/staff")
        app.dependency_overrides[get_db] = lambda: db
        token = security.create_access_token(own.username, own.project_id, role="user")
        headers = {"Authorization": f"Bearer {token}"}
        yield app, db, own, other, headers, tmp_path
    engine.dispose()


@pytest.mark.asyncio
async def test_ordinary_member_can_change_only_their_own_nickname(profile_app):
    app, db, own, other, headers, _ = profile_app
    before = (own.username, own.role, own.password_hash, own.token_version, own.status)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.patch(
            "/staff/me", json={"nickname": "  澜月客服小月  "}, headers=headers
        )
    assert response.status_code == 200, response.text
    assert response.json()["nickname"] == "澜月客服小月"
    db.refresh(other)
    assert other.nickname == "其他企业"
    assert before == (
        own.username,
        own.role,
        own.password_hash,
        own.token_version,
        own.status,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"nickname": " "},
        {"nickname": "x" * 101},
        {"nickname": "新昵称", "role": "admin"},
        {"nickname": "新昵称", "avatar_url": "javascript:alert(1)"},
        {"nickname": "新昵称", "project_id": "another-company"},
    ],
)
async def test_invalid_or_privileged_fields_are_rejected(profile_app, payload):
    app, _, own, _, headers, _ = profile_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.patch("/staff/me", json=payload, headers=headers)
    assert response.status_code == 422
    assert own.nickname == "旧昵称" and own.role == "user"


@pytest.mark.asyncio
async def test_avatar_and_nickname_save_together_and_public_image_is_readable(
    profile_app,
):
    app, _, own, other, headers, root = profile_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/staff/me/avatar",
            headers=headers,
            data={"nickname": "澜月客服"},
            files={"file": ("../../other.png", PNG, "image/png")},
        )
        assert response.status_code == 200, response.text
        url = response.json()["avatar_url"]
        image = await client.get("/staff/" + urlsplit(url).path.split("/staff/", 1)[1])
    assert own.nickname == "澜月客服" and other.avatar_url is None
    assert image.status_code == 200 and image.content == PNG
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert len(list(root.rglob("*.png"))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["too_large", "fake_image", "invalid_nickname"])
async def test_rejected_avatar_preserves_previous_profile(
    profile_app, monkeypatch, kind
):
    app, _, own, _, headers, root = profile_app
    monkeypatch.setattr(staff_profile_service, "STAFF_AVATAR_MAX_BYTES", len(PNG))
    content = (
        PNG + b"x"
        if kind == "too_large"
        else b"not-an-image"
        if kind == "fake_image"
        else PNG
    )
    nickname = " " if kind == "invalid_nickname" else "不会保存"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/staff/me/avatar",
            headers=headers,
            data={"nickname": nickname},
            files={"file": ("avatar.png", content, "image/png")},
        )
    assert response.status_code == (
        413 if kind == "too_large" else 422 if kind == "invalid_nickname" else 400
    )
    assert own.nickname == "旧昵称" and own.avatar_url is None
    assert not list(root.rglob("*.png"))


@pytest.mark.asyncio
async def test_commit_failure_removes_new_file_and_keeps_previous_profile(profile_app):
    app, db, own, _, headers, root = profile_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        with patch.object(
            db, "commit", side_effect=RuntimeError("fixture commit failure")
        ):
            response = await client.post(
                "/staff/me/avatar",
                headers=headers,
                data={"nickname": "不会保存"},
                files={"file": ("avatar.png", PNG, "image/png")},
            )
    assert response.status_code == 500
    db.refresh(own)
    assert own.nickname == "旧昵称" and own.avatar_url is None
    assert not list(root.rglob("*.png"))


@pytest.mark.asyncio
async def test_profile_edits_require_authentication(profile_app):
    app, _, own, _, _, _ = profile_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.patch("/staff/me", json={"nickname": "不会保存"})
    assert response.status_code in {401, 403}
    assert own.nickname == "旧昵称"

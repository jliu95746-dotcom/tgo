"""Avatar size errors must preserve the previous image and return HTTP 413."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.api.v1.endpoints import visitors
from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Staff, Visitor


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_bytes", [-1, 0, 1])
async def test_avatar_upload_size_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_bytes: int,
) -> None:
    project_id, visitor_id = uuid4(), uuid4()
    old_url = f"/v1/visitors/{visitor_id}/avatar"
    visitor = Visitor(
        id=visitor_id, project_id=project_id, avatar_url=old_url,
        updated_at=datetime.now(timezone.utc),
    )
    staff = Staff(id=uuid4(), project_id=project_id, role="admin")
    db = MagicMock(spec=Session)
    db.query.return_value.filter.return_value.first.return_value = visitor
    monkeypatch.setattr(visitors.settings, "UPLOAD_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(visitors.visitor_service, "AVATAR_MAX_SIZE_MB", 1)
    directory = tmp_path / "avatars" / str(project_id) / str(visitor_id)
    directory.mkdir(parents=True)
    previous = directory / "old.png"
    previous.write_bytes(b"previous-test-avatar")

    app = FastAPI()
    app.include_router(visitors.router, prefix="/visitors")
    app.dependency_overrides[get_current_active_user] = lambda: staff
    app.dependency_overrides[get_db] = lambda: db
    payload = b"x" * (1024 * 1024 + extra_bytes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            f"/visitors/{visitor_id}/avatar",
            files={"file": ("avatar.png", payload, "image/png")},
        )

    if extra_bytes > 0:
        assert response.status_code == 413
        assert "1MB" in response.json()["detail"]
        assert previous.read_bytes() == b"previous-test-avatar"
        assert list(directory.iterdir()) == [previous]
        assert visitor.avatar_url == old_url
        db.commit.assert_not_called()
    else:
        assert response.status_code == 200
        assert response.json()["file_size"] == len(payload)
        assert not previous.exists()
        assert len(list(directory.iterdir())) == 1
        db.commit.assert_called_once()

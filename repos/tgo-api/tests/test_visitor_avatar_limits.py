"""Avatar size errors must preserve the previous image and return HTTP 413."""

from pathlib import Path

import httpx
import pytest

from app.api.v1.endpoints import visitors
from app.core.security import create_access_token
from tests.test_im_access import records as records
from tests.test_visitor_profile_access import profile_api as profile_api


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_bytes", [-1, 0, 1])
async def test_avatar_upload_size_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra_bytes: int, profile_api,
) -> None:
    app, db, _, staff, _, visitors_list, _, _, _, _ = profile_api
    visitor = visitors_list[0]
    project_id, visitor_id = visitor.project_id, visitor.id
    old_url = f"/v1/visitors/{visitor_id}/avatar"
    visitor.avatar_url = old_url
    db.commit()
    headers = {"Authorization": "Bearer " + create_access_token(staff[0].username, project_id, staff[0].role)}
    monkeypatch.setattr(visitors.settings, "UPLOAD_BASE_DIR", str(tmp_path))
    monkeypatch.setattr(visitors.visitor_service, "AVATAR_MAX_SIZE_MB", 1)
    directory = tmp_path / "avatars" / str(project_id) / str(visitor_id)
    directory.mkdir(parents=True)
    previous = directory / "old.png"
    previous.write_bytes(b"previous-test-avatar")

    payload = b"x" * (1024 * 1024 + extra_bytes)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(
            f"/visitors/{visitor_id}/avatar",
            files={"file": ("avatar.png", payload, "image/png")},
            headers=headers,
        )

    if extra_bytes > 0:
        assert response.status_code == 413
        assert "1MB" in response.text
        assert previous.read_bytes() == b"previous-test-avatar"
        assert list(directory.iterdir()) == [previous]
        assert visitor.avatar_url == old_url
    else:
        assert response.status_code == 200
        assert response.json()["file_size"] == len(payload)
        assert not previous.exists()
        assert len(list(directory.iterdir())) == 1
        assert visitor.avatar_url.startswith(old_url + "?v=")

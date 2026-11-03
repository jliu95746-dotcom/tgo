from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.db.base import get_db
from app.main import app


@pytest.mark.asyncio
async def test_internal_media_failure_counts_require_service_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings, "saas_internal_token", SecretStr("test-service-token")
    )

    class Result:
        def all(self) -> list[tuple[str | None, int]]:
            return [(None, 2), ("submitted", 3), ("unknown", 1)]

    class Session:
        statement: object | None = None

        async def execute(self, _: object) -> Result:
            self.statement = _
            return Result()

    session = Session()

    async def fake_db():
        yield session

    app.dependency_overrides[get_db] = fake_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/internal/wecom/media-failure-notices")
            allowed = await client.get(
                "/internal/wecom/media-failure-notices",
                headers={"X-SaaS-Service-Token": "test-service-token"},
            )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert allowed.json() == {
        "pending": 2,
        "sending": 0,
        "submitted": 3,
        "unknown": 1,
        "needs_attention": 0,
    }
    assert session.statement is not None
    query = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "dead" in query and "needs_attention" in query

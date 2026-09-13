"""Bind code creation/consumption must each use one atomic Redis operation."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services.bind_code_service import BindCodeService


@pytest.mark.asyncio
async def test_creation_includes_expiry_in_atomic_set(monkeypatch):
    service = BindCodeService()
    service.redis = AsyncMock()
    service.redis.set.return_value = True
    monkeypatch.setattr(service, "_generate_code", lambda: "ABC123")
    project_id = uuid.uuid4()
    code, _ = await service.generate(project_id)
    assert code == "ABC123"
    service.redis.set.assert_awaited_once_with(
        "dc:bind_code:ABC123",
        str(project_id),
        nx=True,
        ex=settings.BIND_CODE_EXPIRY_MINUTES * 60,
    )
    service.redis.expire.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, "invalid", str(uuid.uuid4())])
async def test_consumption_is_single_atomic_operation(stored):
    service = BindCodeService()
    service.redis = AsyncMock()
    service.redis.eval.return_value = stored
    result = await service.validate("abc123")
    assert result == (uuid.UUID(stored) if stored and stored != "invalid" else None)
    service.redis.eval.assert_awaited_once()
    args = service.redis.eval.call_args.args
    assert args[1:] == (1, "dc:bind_code:ABC123")
    assert "GET" in args[0] and "DEL" in args[0]
    service.redis.get.assert_not_awaited()
    service.redis.delete.assert_not_awaited()
    service.redis.keys.assert_not_awaited()


@pytest.mark.asyncio
async def test_redis_failure_is_closed_and_does_not_log_secret(caplog):
    service = BindCodeService()
    service.redis = AsyncMock()
    service.redis.eval.side_effect = RuntimeError("sensitive-redis-credential")
    assert await service.validate("ABC123") is None
    assert "sensitive-redis-credential" not in caplog.text
    assert "ABC123" not in caplog.text

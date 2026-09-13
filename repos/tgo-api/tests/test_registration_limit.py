"""Registration uses a shared Redis limit and fails closed on infrastructure errors."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from app.services import registration_limit


@pytest.mark.asyncio
@pytest.mark.parametrize("count,expected", [(1, None), (5, None), (6, 429)])
async def test_shared_registration_limit(monkeypatch, count, expected):
    redis = AsyncMock()
    redis.eval.return_value = count
    manager = AsyncMock()
    manager.__aenter__.return_value = redis
    monkeypatch.setattr(
        registration_limit.Redis, "from_url", MagicMock(return_value=manager)
    )
    monkeypatch.setattr(
        registration_limit.settings, "REDIS_URL", "redis://fixture.invalid/0"
    )
    if expected:
        with pytest.raises(HTTPException) as error:
            await registration_limit.limit_registration("203.0.113.9")
        assert error.value.status_code == expected
        assert error.value.headers["Retry-After"] == "600"
    else:
        await registration_limit.limit_registration("203.0.113.9")
    args = redis.eval.await_args.args
    assert args[1] == 1 and args[3] == 600
    assert args[2].startswith("registration:attempts:")
    assert "203.0.113.9" not in args[2]
    assert "INCR" in args[0] and "EXPIRE" in args[0]


@pytest.mark.asyncio
async def test_registration_limit_has_no_unbounded_memory_fallback(monkeypatch):
    monkeypatch.setattr(registration_limit.settings, "REDIS_URL", None)
    with pytest.raises(HTTPException) as error:
        await registration_limit.limit_registration("203.0.113.9")
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_redis_failure_returns_safe_service_unavailable(monkeypatch):
    monkeypatch.setattr(
        registration_limit.settings, "REDIS_URL", "redis://fixture.invalid/0"
    )
    monkeypatch.setattr(
        registration_limit.Redis,
        "from_url",
        MagicMock(side_effect=RedisError("private transport details")),
    )
    with pytest.raises(HTTPException) as error:
        await registration_limit.limit_registration("203.0.113.9")
    assert error.value.status_code == 503
    assert "private" not in error.value.detail

from __future__ import annotations

import logging

import httpx
import pytest

from app.api import wecom_utils


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        assert ex is not None and ex >= 60
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class TokenClient:
    calls: list[httpx.Request] = []
    status_code = 200

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "TokenClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(
        self,
        url: str,
        *,
        params: dict[str, str],
    ) -> httpx.Response:
        request = httpx.Request("GET", url, params=params)
        self.calls.append(request)
        return httpx.Response(
            self.status_code,
            request=request,
            json={
                "errcode": 0,
                "access_token": f"token-{len(self.calls)}",
                "expires_in": 7200,
            },
        )


@pytest.mark.asyncio
async def test_token_cache_is_isolated_by_corp_and_app_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    TokenClient.calls = []
    TokenClient.status_code = 200

    async def fake_redis() -> FakeRedis:
        return redis

    monkeypatch.setattr(wecom_utils, "get_redis_client", fake_redis)
    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", TokenClient)

    first = await wecom_utils.wecom_get_access_token("corp", "secret-one")
    cached = await wecom_utils.wecom_get_access_token("corp", "secret-one")
    second_app = await wecom_utils.wecom_get_access_token("corp", "secret-two")

    assert first == cached == "token-1"
    assert second_app == "token-2"
    assert len(TokenClient.calls) == 2
    assert len(redis.values) == 2
    assert all("corp" not in key and "secret" not in key for key in redis.values)

    await wecom_utils.invalidate_wecom_access_token("corp", "secret-one")
    assert len(redis.values) == 1


@pytest.mark.asyncio
async def test_token_http_failure_does_not_expose_secret(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    TokenClient.calls = []
    TokenClient.status_code = 500

    async def no_redis() -> None:
        return None

    monkeypatch.setattr(wecom_utils, "get_redis_client", no_redis)
    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", TokenClient)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(RuntimeError) as error:
        await wecom_utils.wecom_get_access_token("corp", "super-secret")

    assert "super-secret" not in str(error.value)
    assert "super-secret" not in caplog.text
    assert str(error.value) == "WeCom gettoken failed with HTTP 500"

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.api import wecom_utils


class FailingSession:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.rollbacks = 0

    def add(self, _: object) -> None:
        return None

    async def commit(self) -> None:
        raise self.error

    async def rollback(self) -> None:
        self.rollbacks += 1


def integrity_error(sqlstate: str, constraint_name: str) -> IntegrityError:
    original = RuntimeError(f'constraint "{constraint_name}"')
    original.sqlstate = sqlstate
    original.diag = SimpleNamespace(constraint_name=constraint_name)
    return IntegrityError("INSERT", {}, original)


async def store_with(error: IntegrityError) -> wecom_utils.InboxStoreResult:
    return await wecom_utils.try_store_wecom_inbox(
        FailingSession(error),
        platform_id=uuid4(),
        message_id="message-id",
        source_type="wecom_kf",
        from_user="external-user",
        msg_type="text",
        content="hello",
    )


@pytest.mark.asyncio
async def test_only_expected_unique_constraint_is_a_duplicate() -> None:
    duplicate_result = await store_with(
        integrity_error("23505", "uq_wecom_inbox_platform_message")
    )
    foreign_key_result = await store_with(
        integrity_error("23503", "pt_wecom_inbox_platform_id_fkey")
    )

    assert duplicate_result == wecom_utils.InboxStoreResult.DUPLICATE
    assert foreign_key_result == wecom_utils.InboxStoreResult.ERROR


@pytest.mark.asyncio
async def test_inbox_storage_failures_do_not_log_customer_payload(caplog) -> None:
    private_text = "private-customer-message-marker"
    errors = (
        IntegrityError(
            "INSERT INTO inbox(content) VALUES (:content)",
            {"content": private_text},
            RuntimeError("constraint failed"),
        ),
        RuntimeError(private_text),
    )
    for error in errors:
        caplog.clear()
        result = await wecom_utils.try_store_wecom_inbox(
            FailingSession(error),
            platform_id=uuid4(),
            message_id="message-id",
            source_type="wecom_kf",
            from_user="external-user",
            msg_type="text",
            content=private_text,
            raw_payload={"text": private_text},
        )
        assert result == wecom_utils.InboxStoreResult.ERROR
        assert private_text not in caplog.text


@pytest.mark.asyncio
async def test_duplicate_media_repair_failure_does_not_log_provider_data(
    caplog,
) -> None:
    private_text = "private-media-reference-marker"

    class ReadFailure:
        async def scalar(self, _statement):
            raise RuntimeError(private_text)

        async def rollback(self) -> None:
            return None

    reference = wecom_utils.WeComMediaReference(
        source_media_id=private_text,
        media_type="image",
        supported=True,
    )
    result = await wecom_utils._ensure_duplicate_media_state(
        ReadFailure(),
        platform_id=uuid4(),
        message_id="message-id",
        media_reference=reference,
    )
    assert result is False
    assert private_text not in caplog.text


@pytest.mark.asyncio
async def test_changed_duplicate_media_does_not_log_message_identifier(
    caplog,
) -> None:
    private_id = "private-message-id-marker"

    class ExistingMediaSession:
        def __init__(self) -> None:
            self.values = iter(
                (
                    uuid4(),
                    SimpleNamespace(
                        source_media_id="canonical-media-id",
                        media_type="image",
                    ),
                )
            )

        async def scalar(self, _statement):
            return next(self.values)

    result = await wecom_utils._ensure_duplicate_media_state(
        ExistingMediaSession(),
        platform_id=uuid4(),
        message_id=private_id,
        media_reference=wecom_utils.WeComMediaReference(
            source_media_id="changed-media-id",
            media_type="image",
            supported=True,
        ),
    )
    assert result is True
    assert private_id not in caplog.text


@pytest.mark.asyncio
async def test_token_cache_errors_do_not_log_credentials(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    secret = "private-app-secret-marker"

    class FailingRedis:
        async def get(self, _key: str) -> str:
            raise RuntimeError(secret)

        async def set(self, _key: str, _value: str, *, ex: int) -> None:
            raise RuntimeError(secret)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"errcode": 0, "access_token": "test-access-token"}

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _url: str, *, params: object) -> FakeResponse:
            return FakeResponse()

    async def fake_redis() -> FailingRedis:
        return FailingRedis()

    monkeypatch.setattr(wecom_utils, "get_redis_client", fake_redis)
    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", FakeClient)
    token = await wecom_utils.wecom_get_access_token("corp-id", secret)
    assert token == "test-access-token"
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_visitor_cache_error_does_not_log_visitor_id(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    visitor_id = "private-visitor-id-marker"

    class FailingRedis:
        async def get(self, _key: str) -> str:
            raise RuntimeError(visitor_id)

        async def set(self, _key: str, _value: str, *, ex: int) -> None:
            raise RuntimeError(visitor_id)

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"platform_open_id": "test-platform-open-id"}

    class FakeClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def get(self, _path: str) -> FakeResponse:
            return FakeResponse()

    async def fake_redis() -> FailingRedis:
        return FailingRedis()

    monkeypatch.setattr(wecom_utils, "get_redis_client", fake_redis)
    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", FakeClient)
    result = await wecom_utils.resolve_visitor_platform_open_id(visitor_id)
    assert result == "test-platform-open-id"
    assert visitor_id not in caplog.text

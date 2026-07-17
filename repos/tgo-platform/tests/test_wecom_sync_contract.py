from __future__ import annotations

from uuid import uuid4
from types import SimpleNamespace

import pytest

from app.api import wecom_utils


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_sync_stores_only_customer_messages_and_advances_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[dict[str, object]] = []
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 0,
            "msg_list": [
                {
                    "msgid": "customer-message",
                    "origin": 3,
                    "open_kfid": "wk-test",
                    "external_userid": "external-user",
                    "send_time": 1710000000,
                    "msgtype": "text",
                    "text": {"content": "hello"},
                },
                {
                    "msgid": "servicer-message",
                    "origin": 5,
                    "open_kfid": "wk-test",
                    "external_userid": "external-user",
                    "servicer_userid": "staff-user",
                    "send_time": 1710000001,
                    "msgtype": "text",
                    "text": {"content": "staff reply"},
                },
            ],
        }

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored.append(kwargs)
        return wecom_utils.InboxStoreResult.STORED

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist_cursor(
        *_: object,
        **__: object,
    ) -> None:
        persisted_cursors.append("cursor-2")

    monkeypatch.setattr(wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token"))
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fake_store)

    await wecom_utils.sync_kf_messages(
        corp_id="corp-id",
        app_secret="app-secret",
        event_token="event-token",
        open_kf_id="wk-test",
        platform_id=uuid4(),
        db=FakeSession(),
    )

    assert [item["message_id"] for item in stored] == ["customer-message"]
    assert stored[0]["content"] == "hello"
    assert persisted_cursors == ["cursor-2"]


@pytest.mark.asyncio
async def test_sync_does_not_advance_cursor_when_message_storage_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 0,
            "msg_list": [
                {
                    "msgid": "customer-message",
                    "origin": 3,
                    "external_userid": "external-user",
                    "msgtype": "text",
                    "text": {"content": "hello"},
                }
            ],
        }

    async def fail_store(_: object, **__: object) -> wecom_utils.InboxStoreResult:
        return wecom_utils.InboxStoreResult.ERROR

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist_cursor(*_: object, **__: object) -> None:
        persisted_cursors.append("cursor-2")

    monkeypatch.setattr(wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token"))
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fail_store)

    with pytest.raises(RuntimeError, match="persist"):
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id",
            app_secret="app-secret",
            event_token="event-token",
            open_kf_id="wk-test",
            platform_id=uuid4(),
            db=FakeSession(),
        )

    assert persisted_cursors == []


@pytest.mark.asyncio
async def test_sync_requeues_when_page_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 1,
            "msg_list": [],
        }

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist_cursor(*_: object, **__: object) -> None:
        persisted_cursors.append("cursor-2")

    monkeypatch.setattr(wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token"))
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)

    with pytest.raises(wecom_utils.WeComSyncContinuation):
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id",
            app_secret="app-secret",
            event_token="event-token",
            open_kf_id="wk-test",
            platform_id=uuid4(),
            db=FakeSession(),
            max_iters=1,
        )

    assert persisted_cursors == ["cursor-2"]


@pytest.mark.asyncio
async def test_invalid_media_is_quarantined_without_blocking_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[dict[str, object]] = []
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0,
            "next_cursor": "cursor-after-invalid-media",
            "has_more": 0,
            "msg_list": [
                {
                    "msgid": "invalid-image",
                    "origin": 3,
                    "external_userid": "external-user",
                    "msgtype": "image",
                    "image": {},
                }
            ],
        }

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored.append(kwargs)
        return wecom_utils.InboxStoreResult.STORED

    async def fake_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist(*_: object, **__: object) -> None:
        persisted_cursors.append("cursor-after-invalid-media")

    monkeypatch.setattr(
        wecom_utils,
        "wecom_get_access_token",
        lambda *_: _async_value("access-token"),
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fake_store)

    await wecom_utils.sync_kf_messages(
        corp_id="corp-id",
        app_secret="app-secret",
        event_token="event-token",
        open_kf_id="wk-test",
        platform_id=uuid4(),
        db=FakeSession(),
    )

    assert stored[0]["status"] == "media_failed"
    assert stored[0]["media_reference"] is None
    assert "missing media_id" in str(stored[0]["error_message"])
    assert persisted_cursors == ["cursor-after-invalid-media"]


async def _async_value(value: object) -> object:
    return value


@pytest.mark.asyncio
async def test_sync_request_asks_wecom_for_amr_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_payloads: list[dict[str, object]] = []

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def post(
            self,
            _: str,
            *,
            json: dict[str, object],
        ) -> SimpleNamespace:
            sent_payloads.append(json)
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"errcode": 0},
            )

    monkeypatch.setattr(wecom_utils.httpx, "AsyncClient", FakeClient)

    await wecom_utils.wecom_kf_sync_msg(
        "access-token",
        "open-kfid",
        "",
        "event-token",
    )

    assert sent_payloads == [
        {
            "open_kfid": "open-kfid",
            "cursor": "",
            "limit": 500,
            "voice_format": 0,
            "token": "event-token",
        }
    ]

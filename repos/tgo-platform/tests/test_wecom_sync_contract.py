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


_MISSING = object()


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
async def test_sync_keeps_valid_text_with_malformed_optional_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: list[dict[str, object]] = []
    persisted: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0, "next_cursor": "after-message", "has_more": 0,
            "msg_list": [{
                "msgid": "text-with-bad-time", "origin": 3,
                "external_userid": "external-user",
                "send_time": "not-a-timestamp", "msgtype": "text",
                "text": {"content": "请问售后政策"},
            }],
        }

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored.append(kwargs)
        return wecom_utils.InboxStoreResult.STORED

    async def fake_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist(*args: object) -> None:
        persisted.append(str(args[4]))

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token",
        lambda *_: _async_value("access-token"),
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fake_store)

    await wecom_utils.sync_kf_messages(
        corp_id="corp-id", app_secret="app-secret",
        event_token="event-token", open_kf_id="wk-test",
        platform_id=uuid4(), db=FakeSession(),
    )
    assert len(stored) == 1
    assert stored[0]["content"] == "请问售后政策"
    assert stored[0]["received_at"] is None
    assert persisted == ["after-message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", [_MISSING, None, "3", True, 3.5])
async def test_sync_rejects_unknown_origin_before_advancing_cursor(
    monkeypatch: pytest.MonkeyPatch,
    origin: object,
) -> None:
    stored_ids: list[str] = []
    persisted: list[str] = []
    unclassified: dict[str, object] = {
        "msgid": "needs-classification",
        "external_userid": "external-user",
        "msgtype": "text", "text": {"content": "客户问题"},
    }
    if origin is not _MISSING:
        unclassified["origin"] = origin

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        return {
            "errcode": 0, "next_cursor": "after-page", "has_more": 0,
            "msg_list": [unclassified],
        }

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored_ids.append(str(kwargs["message_id"]))
        return wecom_utils.InboxStoreResult.STORED

    async def fake_cursor(*_: object, **__: object) -> str:
        return "before-page"

    async def fake_persist(*args: object) -> None:
        persisted.append(str(args[4]))

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token",
        lambda *_: _async_value("access-token"),
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fake_store)

    with pytest.raises(RuntimeError, match="invalid origin"):
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id", app_secret="app-secret",
            event_token="event-token", open_kf_id="wk-test",
            platform_id=uuid4(), db=FakeSession(),
        )
    assert stored_ids == []
    assert persisted == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "msg_list",
    [
        _MISSING,
        None,
        {"msgid": "customer-message", "origin": 3},
        [
            {"msgid": "customer-message", "origin": 3},
            "malformed-message",
        ],
    ],
)
async def test_sync_rejects_malformed_message_page_before_advancing_cursor(
    monkeypatch: pytest.MonkeyPatch,
    msg_list: object,
) -> None:
    persisted_cursors: list[str] = []
    stored_ids: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        response = {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 0,
            "msg_list": msg_list,
        }
        if msg_list is _MISSING:
            del response["msg_list"]
        return response

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored_ids.append(str(kwargs["message_id"]))
        return wecom_utils.InboxStoreResult.STORED

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return "cursor-1"

    async def fake_persist_cursor(
        _: object,
        __: object,
        ___: object,
        ____: object,
        cursor: str,
        _____: object,
    ) -> None:
        persisted_cursors.append(cursor)

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token")
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", fake_store)

    with pytest.raises(RuntimeError, match="msg_list"):
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id",
            app_secret="app-secret",
            event_token="event-token",
            open_kf_id="wk-test",
            platform_id=uuid4(),
            db=FakeSession(),
        )

    assert persisted_cursors == []
    assert stored_ids == []


@pytest.mark.asyncio
async def test_sync_rejects_nonadvancing_cursor_when_more_pages_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sync_calls: list[str] = []
    persisted_cursors: list[str] = []

    async def fake_sync(
        _: str, __: str, cursor: str, **___: object
    ) -> dict[str, object]:
        sync_calls.append(cursor)
        return {"errcode": 0, "next_cursor": cursor, "has_more": 1, "msg_list": []}

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return "cursor-1"

    async def fake_persist_cursor(
        _: object,
        __: object,
        ___: object,
        ____: object,
        cursor: str,
        _____: object,
    ) -> None:
        persisted_cursors.append(cursor)

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token")
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)

    with pytest.raises(RuntimeError, match="next_cursor"):
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id",
            app_secret="app-secret",
            event_token="event-token",
            open_kf_id="wk-test",
            platform_id=uuid4(),
            db=FakeSession(),
            max_iters=2,
        )

    assert sync_calls == ["cursor-1"]
    assert persisted_cursors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("has_more", [_MISSING, None, -1, 2, "0"])
async def test_sync_rejects_invalid_pagination_flag_without_advancing_cursor(
    monkeypatch: pytest.MonkeyPatch,
    has_more: object,
) -> None:
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        response: dict[str, object] = {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "msg_list": [],
        }
        if has_more is not _MISSING:
            response["has_more"] = has_more
        return response

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return "cursor-1"

    async def fake_persist_cursor(
        _: object,
        __: object,
        ___: object,
        ____: object,
        cursor: str,
        _____: object,
    ) -> None:
        persisted_cursors.append(cursor)

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token")
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)

    with pytest.raises(RuntimeError, match="has_more"):
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
@pytest.mark.parametrize("errcode", [_MISSING, None, "0", 40001])
async def test_sync_rejects_provider_error_without_exposing_response_data(
    monkeypatch: pytest.MonkeyPatch,
    errcode: object,
) -> None:
    private_content = "customer-private-content"
    persisted_cursors: list[str] = []

    async def fake_sync(*_: object, **__: object) -> dict[str, object]:
        response: dict[str, object] = {
            "errmsg": private_content,
            "next_cursor": "cursor-2",
            "has_more": 0,
            "msg_list": [],
        }
        if errcode is not _MISSING:
            response["errcode"] = errcode
        return response

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return "cursor-1"

    async def fake_persist_cursor(
        _: object,
        __: object,
        ___: object,
        ____: object,
        cursor: str,
        _____: object,
    ) -> None:
        persisted_cursors.append(cursor)

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token")
    )
    monkeypatch.setattr(wecom_utils, "get_wecom_sync_cursor", fake_get_cursor)
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", fake_persist_cursor)
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", fake_sync)

    with pytest.raises(RuntimeError, match="errcode") as error:
        await wecom_utils.sync_kf_messages(
            corp_id="corp-id",
            app_secret="app-secret",
            event_token="event-token",
            open_kf_id="wk-test",
            platform_id=uuid4(),
            db=FakeSession(),
        )

    assert private_content not in str(error.value)
    assert persisted_cursors == []


@pytest.mark.asyncio
async def test_sync_paginates_one_thousand_mixed_messages_without_losing_customers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[dict[str, object]] = []
    for index in range(1000):
        message: dict[str, object] = {
            "msgid": f"message-{index}",
            "origin": 5 if index % 4 == 3 else 3,
            "external_userid": "external-user",
            "send_time": 1710000000 + index,
        }
        if index % 4 == 0:
            message.update(msgtype="text", text={"content": f"question-{index}"})
        elif index % 4 == 1:
            message.update(msgtype="image", image={"media_id": f"image-{index}"})
        elif index % 4 == 2:
            message.update(msgtype="voice", voice={"media_id": f"voice-{index}"})
        else:
            message.update(msgtype="text", text={"content": "staff reply"})
        messages.append(message)

    requested_cursors: list[str] = []
    persisted_cursors: list[str] = []
    stored: list[tuple[str, str]] = []

    async def fake_sync(
        _: str,
        __: str,
        cursor: str,
        **___: object,
    ) -> dict[str, object]:
        requested_cursors.append(cursor)
        if cursor == "":
            return {
                "errcode": 0,
                "next_cursor": "cursor-1",
                "has_more": 1,
                "msg_list": messages[:500],
            }
        assert cursor == "cursor-1"
        return {
            "errcode": 0,
            "next_cursor": "cursor-2",
            "has_more": 0,
            "msg_list": messages[500:],
        }

    async def fake_store(_: object, **kwargs: object) -> wecom_utils.InboxStoreResult:
        stored.append((str(kwargs["message_id"]), str(kwargs["status"])))
        return wecom_utils.InboxStoreResult.STORED

    async def fake_get_cursor(*_: object, **__: object) -> str:
        return ""

    async def fake_persist_cursor(
        _: object,
        __: object,
        ___: object,
        ____: object,
        cursor: str,
        _____: object,
    ) -> None:
        persisted_cursors.append(cursor)

    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", lambda *_: _async_value("access-token")
    )
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
        max_iters=2,
    )

    assert requested_cursors == ["", "cursor-1"]
    assert persisted_cursors == ["cursor-1", "cursor-2"]
    assert len(stored) == len(set(message_id for message_id, _ in stored)) == 750
    assert sum(status == "pending" for _, status in stored) == 250
    assert sum(status == "pending_media" for _, status in stored) == 500


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

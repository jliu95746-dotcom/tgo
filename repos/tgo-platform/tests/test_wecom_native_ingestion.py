from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api import wecom_utils


@pytest.mark.asyncio
async def test_enabled_native_account_persists_employee_messages_and_session_events(
    monkeypatch,
):
    stored = []

    async def store(_db, **values):
        stored.append(values)
        return wecom_utils.InboxStoreResult.STORED

    page = {
        "errcode": 0,
        "next_cursor": "native-next",
        "has_more": 0,
        "msg_list": [
            {
                "msgid": "staff-one",
                "origin": 5,
                "open_kfid": "kf-one",
                "external_userid": "customer-one",
                "servicer_userid": "employee-one",
                "msgtype": "text",
                "text": {"content": "人工已记录黑色偏好"},
            },
            {
                "msgid": "state-one",
                "origin": 4,
                "msgtype": "event",
                "event": {
                    "event_type": "session_status_change",
                    "open_kfid": "kf-one",
                    "external_userid": "customer-one",
                    "change_type": 3,
                },
            },
        ],
    }
    monkeypatch.setattr(
        wecom_utils, "wecom_get_access_token", AsyncMock(return_value="test-only-token")
    )
    monkeypatch.setattr(
        wecom_utils, "get_wecom_sync_cursor", AsyncMock(return_value="before")
    )
    monkeypatch.setattr(wecom_utils, "persist_wecom_sync_cursor", AsyncMock())
    monkeypatch.setattr(wecom_utils, "wecom_kf_sync_msg", AsyncMock(return_value=page))
    monkeypatch.setattr(wecom_utils, "try_store_wecom_inbox", store)
    await wecom_utils.sync_kf_messages(
        corp_id="test-corp",
        app_secret="test-only-secret",
        event_token="test-event",
        open_kf_id="kf-one",
        platform_id=uuid4(),
        db=SimpleNamespace(),
        include_native_context=True,
    )
    assert [row["message_id"] for row in stored] == ["staff-one", "state-one"]
    assert stored[0]["is_from_colleague"] is True
    assert stored[1]["status"] == "pending"
    assert stored[0]["raw_payload"]["kf_sync_msg"]["origin"] == 5

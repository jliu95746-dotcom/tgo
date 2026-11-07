"""Message mapping needs normalized columns, not the original event payload."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import make_transient_to_detached

from app.db.models import FeishuInbox
from app.domain.services.listeners.feishu_listener import (
    FeishuChannelListener,
    FeishuPlatformConfig,
    _PlatformEntry,
)


@pytest.fixture
def mapping_case():
    platform = _PlatformEntry(
        id=uuid4(),
        project_id=uuid4(),
        api_key="fixture-platform-key",
        cfg=FeishuPlatformConfig(
            app_id="fixture-app-id",
            app_secret="fixture-app-secret",
        ),
    )
    record = FeishuInbox(
        id=uuid4(),
        platform_id=platform.id,
        message_id="fixture-message",
        from_user="fixture-user",
        from_user_type="open_id",
        chat_id=None,
        chat_type=None,
        msg_type="text",
        content="商品材质是什么？",
    )
    expected = {
        "source": "feishu",
        "from_uid": "fixture-user",
        "content": "商品材质是什么？",
        "platform_api_key": "fixture-platform-key",
        "platform_type": "feishu_bot",
        "platform_id": str(platform.id),
        "extra": {
            "project_id": str(platform.project_id),
            "message_id": "fixture-message",
            "msg_type": "text",
            "feishu": {
                "message_id": "fixture-message",
                "chat_id": "",
                "chat_type": "p2p",
                "msg_type": "text",
                "from_user_type": "open_id",
                "app_id": "fixture-app-id",
                "app_secret": "fixture-app-secret",
            },
        },
    }
    return SimpleNamespace(
        listener=object.__new__(FeishuChannelListener),
        platform=platform,
        record=record,
        expected=expected,
    )


@pytest.mark.parametrize("payload", [None, {"private-event": "unused-value"}])
def test_loaded_payload_does_not_change_normalized_message(mapping_case, payload):
    case = mapping_case
    case.record.raw_payload = payload
    result = case.listener._build_mapped_message(case.platform, case.record)
    assert result == case.expected


def test_mapping_does_not_fetch_unloaded_event_payload(mapping_case):
    case = mapping_case
    make_transient_to_detached(case.record)
    assert "raw_payload" in inspect(case.record).unloaded
    result = case.listener._build_mapped_message(case.platform, case.record)
    assert result == case.expected
    assert "raw_payload" in inspect(case.record).unloaded

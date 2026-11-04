from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.domain.services.wecom_app_notifications import AppNotifier, notification_url


def fixture_notifier():
    config = SimpleNamespace(
        app_notification_enabled=True,
        app_notification_users=["employee"],
        app_notification_base_url="https://support.example.com",
        corp_id="corp",
        app_secret="app-test-secret",
        agent_id="7",
    )
    redis = AsyncMock()
    redis.get.return_value = None
    redis.exists.return_value = 0
    lock = AsyncMock()
    lock.acquire.return_value = True
    redis.lock = Mock(return_value=lock)
    notifier = AppNotifier(uuid4(), config, redis)
    notifier._send = AsyncMock(return_value={"errcode": 0, "msgid": "receipt"})
    return notifier, redis


def test_notification_link_accepts_only_https_origins():
    visitor = uuid4()
    assert notification_url("https://support.example.com/", visitor).endswith(
        f"/chat/251/{visitor}-vtr"
    )
    for base in [
        "http://support.example.com",
        "https://user:pass@host",
        "https://host/path",
        "javascript:alert(1)",
    ]:
        with pytest.raises(ValueError):
            notification_url(base, visitor)


@pytest.mark.asyncio
async def test_assigned_notification_only_goes_to_assigned_employee():
    notifier, redis = fixture_notifier()
    await notifier.deliver("handoff", uuid4(), "episode", ["employee"])
    assert notifier._send.call_args.args[0] == "employee"
    assert "已分配人工" in notifier._send.call_args.args[1]["title"]
    assert any(":sent:" in str(call.args[0]) for call in redis.set.call_args_list)


@pytest.mark.asyncio
async def test_auto_and_unconfigured_recipients_are_never_sent():
    notifier, _ = fixture_notifier()
    await notifier.deliver(
        "handoff", uuid4(), "episode", ["@all", "outsider", "employee|outsider"]
    )
    notifier._send.assert_not_awaited()
    notifier.config.app_notification_enabled = False
    await notifier.deliver("queued", uuid4(), "episode", ["employee"])
    notifier._send.assert_not_awaited()


@pytest.mark.asyncio
async def test_persisted_receipt_suppresses_callback_and_poll_duplicates():
    notifier, redis = fixture_notifier()
    redis.exists.return_value = 1
    await notifier.deliver("queued", uuid4(), "episode", ["employee"])
    notifier._send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        {"errcode": 0, "invaliduser": "employee", "msgid": "x"},
        {"errcode": 0, "unlicenseduser": "employee", "msgid": "x"},
        {"errcode": 0},
        {"errcode": 40003},
    ],
)
async def test_rejected_or_incomplete_provider_receipt_is_not_marked_sent(receipt):
    notifier, redis = fixture_notifier()
    notifier._send.return_value = receipt
    await notifier.deliver("assist", uuid4(), "source", ["employee"])
    assert not any(":sent:" in str(call.args[0]) for call in redis.set.call_args_list)
    assert any(":retry:" in str(call.args[0]) for call in redis.set.call_args_list)


@pytest.mark.asyncio
async def test_network_failure_does_not_interrupt_customer_processing():
    notifier, redis = fixture_notifier()
    notifier._send.side_effect = RuntimeError("provider unavailable")
    await notifier.deliver("handoff", uuid4(), "episode", ["employee"])
    assert not any(":sent:" in str(call.args[0]) for call in redis.set.call_args_list)


@pytest.mark.asyncio
async def test_assist_text_does_not_claim_a_draft_has_been_generated():
    notifier, _ = fixture_notifier()
    await notifier.deliver("assist", uuid4(), "source", ["employee"])
    card = notifier._send.call_args.args[1]
    assert "查看或生成" in card["description"]
    assert "确认发送" in card["description"]
    assert card["btntxt"] == "查看会话"


@pytest.mark.asyncio
async def test_transport_uses_application_secret_and_provider_duplicate_protection(
    monkeypatch,
):
    from app.api import wecom_utils

    notifier, _ = fixture_notifier()
    token = AsyncMock(return_value="test-token")
    send = AsyncMock(return_value={"errcode": 0, "msgid": "receipt"})
    monkeypatch.setattr(wecom_utils, "wecom_get_access_token", token)
    monkeypatch.setattr(wecom_utils, "wecom_send_app_message", send)
    await AppNotifier._send(notifier, "employee", {"title": "test"})
    token.assert_awaited_once_with("corp", "app-test-secret")
    assert send.call_args.kwargs["enable_duplicate_check"] is True
    assert send.call_args.kwargs["duplicate_check_interval"] == 14400


@pytest.mark.asyncio
async def test_queue_then_assignment_share_one_consultation_and_close_resets_it():
    notifier, redis = fixture_notifier()
    redis.get.return_value = "consultation-1"
    notifier.deliver = AsyncMock()
    visitor = uuid4()
    await notifier.native_state(visitor, 2, "")
    await notifier.native_state(visitor, 3, "employee")
    assert [call.args[0] for call in notifier.deliver.call_args_list] == [
        "queued",
        "handoff",
    ]
    assert all(
        call.args[2] == "consultation-1" for call in notifier.deliver.call_args_list
    )
    await notifier.native_state(visitor, 4, "")
    redis.delete.assert_awaited_once_with(notifier.prefix + ":episode:" + str(visitor))
    assert notifier.deliver.await_count == 2


@pytest.mark.asyncio
async def test_assist_burst_is_coalesced_without_marking_newer_source_as_sent():
    notifier, redis = fixture_notifier()
    redis.exists.side_effect = [0, 0, 1]
    await notifier.deliver("assist", uuid4(), "newer-source", ["employee"])
    notifier._send.assert_not_awaited()
    redis.set.assert_not_awaited()

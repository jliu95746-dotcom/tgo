from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.domain.services.wecom_native import (
    NativeBridge,
    NativeProviderError,
    NativeServicer,
    NativeState,
    choose_candidates,
    handle_native_record,
    native_content,
)


def bridge_fixture():
    platform = SimpleNamespace(
        id=uuid4(), cfg=SimpleNamespace(native_open_kfid="kf-1", native_capacity=20)
    )
    bridge = NativeBridge(platform)
    redis = AsyncMock()
    lock = AsyncMock()
    lock.acquire.return_value = True
    redis.lock = Mock(return_value=lock)
    redis.get.return_value = None
    redis.zcard.return_value = 0
    bridge._redis = AsyncMock(return_value=redis)
    bridge.state = AsyncMock(return_value=NativeState(service_state=0))
    bridge.mirror = AsyncMock()
    return bridge, redis


@pytest.mark.asyncio
async def test_no_available_employee_enters_the_provider_queue_without_claiming_assignment():
    bridge, redis = bridge_fixture()
    bridge._provider = AsyncMock(
        return_value={"servicer_list": [{"userid": "offline", "status": 1}]}
    )
    bridge._transition = AsyncMock(return_value=NativeState(service_state=2))
    await bridge.route("customer")
    bridge._transition.assert_awaited_once_with("customer", 2)
    assert bridge.mirror.call_args.kwargs["state"].service_state == 2
    assert redis.zadd.call_args.kwargs["nx"] is True


@pytest.mark.asyncio
async def test_provider_rejected_employee_is_not_reported_as_assigned():
    bridge, _ = bridge_fixture()
    bridge._provider = AsyncMock(
        return_value={"servicer_list": [{"userid": "full", "status": 0}]}
    )
    bridge._transition = AsyncMock(
        side_effect=[NativeProviderError(95014), NativeState(service_state=2)]
    )
    await bridge.route("customer")
    assert bridge.mirror.call_args.kwargs["event_id"] == "native-queued"


@pytest.mark.asyncio
async def test_existing_native_assignment_is_not_duplicated():
    bridge, redis = bridge_fixture()
    bridge.state = AsyncMock(
        return_value=NativeState(service_state=3, servicer_userid="existing")
    )
    bridge._transition = AsyncMock()
    await bridge.route("customer")
    bridge._transition.assert_not_awaited()
    redis.incr.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_web_session_ends_the_provider_assignment_and_clears_queue():
    bridge, redis = bridge_fixture()
    bridge.state.return_value = NativeState(service_state=3, servicer_userid="staff-a")
    bridge._transition = AsyncMock(return_value=NativeState(service_state=4))
    await bridge.release("customer")
    bridge._transition.assert_awaited_once_with("customer", 4)
    assert bridge.mirror.call_args.kwargs["state"].service_state == 4
    redis.zrem.assert_awaited_with(bridge.prefix + ":queue", "customer")


@pytest.mark.asyncio
async def test_new_customer_after_close_returns_to_assistant_without_reassignment():
    bridge, _ = bridge_fixture()
    bridge._transition = AsyncMock(return_value=NativeState(service_state=1))
    await bridge.release("customer")
    bridge._transition.assert_awaited_once_with("customer", 1)


@pytest.mark.asyncio
async def test_already_ended_provider_session_is_not_reopened_by_release():
    bridge, _ = bridge_fixture()
    bridge.state.return_value = NativeState(service_state=4)
    bridge._transition = AsyncMock()
    await bridge.release("customer")
    bridge._transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_waiting_session_does_not_make_an_unsupported_provider_transition():
    bridge, _ = bridge_fixture()
    bridge.state.return_value = NativeState(service_state=2)
    bridge._transition = AsyncMock()
    with pytest.raises(RuntimeError, match="waiting"):
        await bridge.release("customer")
    bridge._transition.assert_not_awaited()
    bridge.mirror.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_releases_closed_sessions_instead_of_routing_them_again():
    bridge, redis = bridge_fixture()
    bridge._internal = AsyncMock(
        return_value={
            "visitors": [{"external_userid": "customer", "release_requested": True}]
        }
    )
    redis.zrange.return_value = ["customer"]
    bridge.release = AsyncMock()
    bridge.route = AsyncMock()
    await bridge.reconcile()
    bridge.release.assert_awaited_once_with("customer")
    bridge.route.assert_not_awaited()
    bridge.mirror.assert_not_awaited()


def test_previous_servicer_has_priority_only_when_accepting_and_below_capacity():
    people = [
        NativeServicer(userid="a", status=0),
        NativeServicer(userid="b", status=0),
    ]
    assert choose_candidates(people, "b", 0, {}, 2) == ["b", "a"]
    assert choose_candidates(people, "b", 0, {"b": 2}, 2) == ["a"]
    assert (
        choose_candidates([NativeServicer(userid="b", status=1)], "b", 0, {}, 2) == []
    )


def test_other_available_staff_rotate_without_changing_the_allowlist():
    people = [NativeServicer(userid=x, status=0) for x in ["a", "b", "c"]]
    assert choose_candidates(people, "removed", 0, {}, 20) == ["a", "b", "c"]
    assert choose_candidates(people, "removed", 1, {}, 20) == ["b", "c", "a"]


def test_staff_attachments_remain_explicit_in_context():
    assert (
        native_content({"msgtype": "text", "text": {"content": "人工确认过的内容"}})
        == "人工确认过的内容"
    )
    assert "图片" in native_content(
        {"msgtype": "image", "image": {"media_id": "image-1"}}
    )


@pytest.mark.asyncio
async def test_disabled_native_bridge_leaves_existing_channels_unchanged():
    platform = SimpleNamespace(cfg=SimpleNamespace(native_handoff_enabled=False))
    assert await handle_native_record(platform, SimpleNamespace(), AsyncMock()) is False


@pytest.mark.asyncio
async def test_staff_messages_are_mirrored_and_never_enter_the_ai_reply_pipeline(
    monkeypatch,
):
    from app.domain.services import wecom_native

    bridge = AsyncMock()
    bridge.__aenter__.return_value = bridge
    monkeypatch.setattr(wecom_native, "NativeBridge", lambda _: bridge)
    platform = SimpleNamespace(
        api_key="test-key",
        project_id=uuid4(),
        cfg=SimpleNamespace(native_handoff_enabled=True, native_open_kfid="kf-1"),
    )
    record = SimpleNamespace(
        open_kfid="kf-1",
        message_id="staff-msg",
        content="人工已经解释过了",
        raw_payload={
            "kf_sync_msg": {
                "open_kfid": "kf-1",
                "origin": 5,
                "external_userid": "customer",
                "servicer_userid": "staff-a",
                "msgtype": "text",
                "text": {"content": "人工已经解释过了"},
            }
        },
    )
    assert await handle_native_record(platform, record, AsyncMock()) is True
    assert bridge.mirror.call_args.kwargs["kind"] == "staff"
    assert bridge.mirror.call_args.kwargs["servicer_userid"] == "staff-a"


@pytest.mark.asyncio
async def test_native_customer_messages_are_saved_without_an_ai_reply(monkeypatch):
    from app.domain.services import wecom_native

    bridge = AsyncMock()
    bridge.__aenter__.return_value = bridge
    bridge.state.return_value = SimpleNamespace(
        service_state=3, servicer_userid="staff-a"
    )
    bridge.mirror.return_value = SimpleNamespace(service_mode="manual")
    monkeypatch.setattr(wecom_native, "NativeBridge", lambda _: bridge)
    platform = SimpleNamespace(
        api_key="test-key",
        project_id=uuid4(),
        cfg=SimpleNamespace(native_handoff_enabled=True, native_open_kfid="kf-1"),
    )
    record = SimpleNamespace(
        open_kfid="kf-1",
        message_id="customer-msg",
        raw_payload={
            "kf_sync_msg": {
                "origin": 3,
                "open_kfid": "kf-1",
                "external_userid": "customer",
                "msgtype": "text",
                "text": {"content": "接着刚才的问题"},
            }
        },
    )
    assert await handle_native_record(platform, record, AsyncMock()) is True
    assert bridge.mirror.call_args.kwargs["kind"] == "customer"


@pytest.mark.asyncio
async def test_next_consultation_enters_ai_after_human_ownership_has_ended(monkeypatch):
    from app.domain.services import wecom_native

    bridge = AsyncMock()
    bridge.__aenter__.return_value = bridge
    bridge.state.return_value = NativeState(service_state=0)
    bridge.mirror.return_value = SimpleNamespace(service_mode="auto")
    monkeypatch.setattr(wecom_native, "NativeBridge", lambda _: bridge)
    platform = SimpleNamespace(
        api_key="test-key",
        project_id=uuid4(),
        cfg=SimpleNamespace(native_handoff_enabled=True, native_open_kfid="kf-1"),
    )
    record = SimpleNamespace(
        open_kfid="kf-1",
        message_id="next-consultation",
        raw_payload={
            "kf_sync_msg": {
                "origin": 3,
                "open_kfid": "kf-1",
                "external_userid": "customer",
                "msgtype": "text",
                "text": {"content": "再次咨询"},
            }
        },
    )
    assert await handle_native_record(platform, record, AsyncMock()) is False
    bridge.route.assert_not_awaited()
    assert bridge.mirror.await_count == 1


@pytest.mark.asyncio
async def test_native_state_lookup_failure_blocks_automatic_reply(monkeypatch):
    from app.domain.services import wecom_native

    bridge = AsyncMock()
    bridge.__aenter__.return_value = bridge
    bridge.state.side_effect = RuntimeError("provider unavailable")
    monkeypatch.setattr(wecom_native, "NativeBridge", lambda _: bridge)
    platform = SimpleNamespace(
        api_key="test-key",
        project_id=uuid4(),
        cfg=SimpleNamespace(native_handoff_enabled=True, native_open_kfid="kf-1"),
    )
    record = SimpleNamespace(
        open_kfid="kf-1",
        message_id="customer-msg",
        raw_payload={
            "kf_sync_msg": {
                "origin": 3,
                "open_kfid": "kf-1",
                "external_userid": "customer",
                "msgtype": "text",
                "text": {"content": "你好"},
            }
        },
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await handle_native_record(platform, record, AsyncMock())

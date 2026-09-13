"""A confirmed delivery owns one durable correction, independent of the browser."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.staff_message_delivery import StaffMessageDelivery
from app.services import staff_delivery
from tests import test_staff_delivery_outbox as outbox_fixtures

# Expose the shared fixtures in this module without pytest plugin import ordering.
db = outbox_fixtures.db
target = outbox_fixtures.target
request = outbox_fixtures.request


def trained_request(target):
    from app.schemas.staff_delivery import AssistTrainingIntent

    return request(
        target,
        training=AssistTrainingIntent(
            skill_name="style-a",
            customer_message="有绿色吗？",
            ai_draft="抱歉，没有绿色。",
            source_message_id="customer-question-1",
            recent_messages=[],
        ),
        payload={"type": 1, "content": "没有绿色。"},
    )


def assist_target(target):
    return target.model_copy(
        update={
            "service_mode": "assist",
            "humanization_skill_name": "style-a",
            "humanization_skill_enabled": True,
        }
    )


@pytest.mark.asyncio
async def test_inherited_employee_training_and_retry_keep_original_target(db, target, monkeypatch):
    from app.schemas.employee_style import EmployeeStyle
    target = target.model_copy(update={"service_mode": "assist", "agent_id": "price-employee"})
    resolver = AsyncMock(return_value=EmployeeStyle(skill_name="style-a", enabled=True))
    monkeypatch.setattr(staff_delivery, "resolve_employee_style", resolver)
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    first = await staff_delivery.deliver(db, target, trained_request(target))
    assert first.training_status == "pending" and first.delivery_status == "sent"
    resolver.side_effect = RuntimeError("employee settings unavailable")
    again = await staff_delivery.deliver(db, target, trained_request(target))
    assert again.delivery_status == "sent"
    resolver.assert_awaited_once_with(str(target.project_id), "price-employee")
    external.assert_awaited_once()


@pytest.mark.asyncio
async def test_employee_binding_change_blocks_wrong_training_before_send(db, target, monkeypatch):
    from app.schemas.employee_style import EmployeeStyle
    target = target.model_copy(update={"service_mode": "assist"})
    monkeypatch.setattr(staff_delivery, "resolve_employee_style", AsyncMock(
        return_value=EmployeeStyle(skill_name="style-b", enabled=True)))
    external = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    with pytest.raises(HTTPException) as raised:
        await staff_delivery.deliver(db, target, trained_request(target))
    assert raised.value.status_code == 409
    external.assert_not_awaited()


@pytest.mark.asyncio
async def test_training_survives_new_session_and_keeps_original_skill(
    db, target, monkeypatch
):
    from app.services import staff_delivery_training

    target = assist_target(target)
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    capture = AsyncMock(
        return_value={
            "name": "style-a",
            "pending_training_count": 1,
            "published_version": 1,
        }
    )
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    first = await staff_delivery.deliver(db, target, trained_request(target))
    assert first.delivery_status == "sent" and first.training_status == "pending"
    capture.assert_not_awaited()
    assert len(staff_delivery.pending_deliveries(db, target)) == 1
    with Session(db.bind) as resumed:
        assert await staff_delivery_training.recover_pending_training(resumed) == 1
        assert await staff_delivery_training.recover_pending_training(resumed) == 0
    capture.assert_awaited_once()
    args = capture.call_args.args
    assert args[0] == str(target.project_id) and args[1] == "style-a"
    assert args[2]["final_reply"] == "没有绿色。"
    assert args[2]["source_message_id"] == "customer-question-1"
    assert args[2]["delivery_id"]
    changed = target.model_copy(
        update={"service_mode": "manual", "humanization_skill_name": "style-b"}
    )
    again = await staff_delivery.deliver(db, changed, trained_request(target))
    assert again.training_status == "saved"
    assert external.await_count == 1


@pytest.mark.asyncio
async def test_unknown_delivery_never_trains(db, target, monkeypatch):
    from app.services import staff_delivery_training

    target = assist_target(target)
    monkeypatch.setattr(
        staff_delivery,
        "forward_staff_platform_message",
        AsyncMock(side_effect=httpx.ReadTimeout("timeout")),
    )
    capture = AsyncMock()
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    result = await staff_delivery.deliver(db, target, trained_request(target))
    assert result.delivery_status == "unknown"
    assert await staff_delivery_training.recover_pending_training(db) == 0
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_training_outage_retries_without_customer_redelivery(
    db, target, monkeypatch
):
    from app.services import staff_delivery_training

    target = assist_target(target)
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    capture = AsyncMock(
        side_effect=[
            RuntimeError("lost response"),
            {"name": "style-a", "pending_training_count": 1, "published_version": 1},
        ]
    )
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    await staff_delivery.deliver(db, target, trained_request(target))
    assert await staff_delivery_training.recover_pending_training(db) == 0
    row = staff_delivery.get_delivery(db, target, "local-test-send-1")
    assert row.training_status == "pending"
    row.training_next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert await staff_delivery_training.recover_pending_training(db) == 1
    assert (
        capture.call_args_list[0].args[2]["delivery_id"]
        == capture.call_args_list[1].args[2]["delivery_id"]
    )
    assert external.await_count == 1


@pytest.mark.asyncio
async def test_training_requires_assist_mode_and_selected_skill_before_delivery(
    db, target, monkeypatch
):
    external = AsyncMock()
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    with pytest.raises(HTTPException) as raised:
        await staff_delivery.deliver(db, target, trained_request(target))
    assert raised.value.status_code == 409
    assert db.query(StaffMessageDelivery).count() == 0
    external.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_delivery_trains_only_after_explicit_accepted_retry(
    db, target, monkeypatch
):
    from app.services import staff_delivery_training

    target = assist_target(target)
    external = AsyncMock(
        side_effect=[
            httpx.Response(422, json={}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    capture = AsyncMock(
        return_value={
            "name": "style-a",
            "pending_training_count": 1,
            "published_version": 1,
        }
    )
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    original = trained_request(target)
    first = await staff_delivery.deliver(db, target, original)
    assert first.delivery_status == "failed"
    assert await staff_delivery_training.recover_pending_training(db) == 0
    retry = await staff_delivery.deliver(
        db, target, original.model_copy(update={"retry_failed": True})
    )
    assert retry.delivery_status == "sent"
    assert await staff_delivery_training.recover_pending_training(db) == 1
    assert await staff_delivery_training.recover_pending_training(db) == 0
    assert external.await_count == 2 and capture.await_count == 1


@pytest.mark.asyncio
async def test_internal_delivery_waits_for_im_confirmation_before_training(
    db, target, monkeypatch
):
    from app.services import staff_delivery_training

    target = assist_target(target).model_copy(update={"platform_type": "website"})
    external = AsyncMock()
    capture = AsyncMock(
        return_value={
            "name": "style-a",
            "pending_training_count": 1,
            "published_version": 1,
        }
    )
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(
        staff_delivery,
        "write_delivery_history",
        AsyncMock(side_effect=[RuntimeError("IM offline"), None]),
    )
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    original = trained_request(target)
    first = await staff_delivery.deliver(db, target, original)
    assert first.delivery_status == "pending"
    assert await staff_delivery_training.recover_pending_training(db) == 0
    await staff_delivery.deliver(db, target, original)
    assert await staff_delivery_training.recover_pending_training(db) == 1
    external.assert_not_awaited()
    assert staff_delivery.pending_deliveries(db, target) == []


@pytest.mark.asyncio
async def test_unchanged_draft_has_no_pending_training(db, target, monkeypatch):
    monkeypatch.setattr(
        staff_delivery,
        "forward_staff_platform_message",
        AsyncMock(return_value=httpx.Response(200, json={"ok": True})),
    )
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    original = trained_request(assist_target(target))
    same = original.model_copy(
        update={"payload": {"type": 1, "content": original.training.ai_draft}}
    )
    result = await staff_delivery.deliver(db, target, same)
    assert result.training_status == "none"
    assert db.query(StaffMessageDelivery).one().training_snapshot is None


@pytest.mark.asyncio
async def test_changed_training_context_cannot_reuse_delivery_identity(
    db, target, monkeypatch
):
    target = assist_target(target)
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    original = trained_request(target)
    await staff_delivery.deliver(db, target, original)
    changed = original.model_copy(
        update={
            "training": original.training.model_copy(update={"skill_name": "style-b"})
        }
    )
    with pytest.raises(HTTPException) as raised:
        await staff_delivery.deliver(db, target, changed)
    assert raised.value.status_code == 409 and external.await_count == 1
    assert (
        db.query(StaffMessageDelivery).one().training_snapshot["skill_name"]
        == "style-a"
    )


@pytest.mark.asyncio
async def test_deleted_skill_preserves_correction_without_retarget_or_redelivery(
    db, target, monkeypatch
):
    from app.services import staff_delivery_training

    target = assist_target(target)
    external = AsyncMock(return_value=httpx.Response(200, json={"ok": True}))
    capture = AsyncMock(side_effect=HTTPException(404, "Deleted skill"))
    monkeypatch.setattr(staff_delivery, "forward_staff_platform_message", external)
    monkeypatch.setattr(staff_delivery, "write_delivery_history", AsyncMock())
    monkeypatch.setattr(
        staff_delivery_training.ai_client, "add_humanization_training_sample", capture
    )
    await staff_delivery.deliver(db, target, trained_request(target))
    assert await staff_delivery_training.recover_pending_training(db) == 0
    assert await staff_delivery_training.recover_pending_training(db) == 0
    record = staff_delivery.pending_deliveries(db, target)[0]
    assert record.receipt.training_status == "unavailable"
    assert record.request.training.skill_name == "style-a"
    assert record.request.payload["content"] == "没有绿色。"
    assert external.await_count == capture.await_count == 1


@pytest.mark.parametrize("content", [True, 12, {"text": "hello"}, ["hello"]])
def test_text_delivery_rejects_non_string_content(target, content):
    from pydantic import ValidationError

    from app.schemas.staff_delivery import StaffDeliveryRequest

    with pytest.raises(ValidationError):
        StaffDeliveryRequest(
            channel_id=target.channel_id,
            client_msg_no="invalid-content",
            payload={"type": 1, "content": content},
        )

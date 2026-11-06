"""Configured timeout fallback keeps the original message and current authority."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Visitor, VisitorSession
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.ai_interaction_run import AIInteractionRun
from app.schemas.wukongim import WuKongIMMessage
from app.services import employee_style, reply_service_mode
from app.services import timed_ai_fallback
from app.services import ai_interaction_run_service as interactions
from app.services.ai_reply_control import ReplyStopped
from app.services.visitor_chat_access import guarded_chat_call
from app.tasks import auto_fallback_to_ai as task
from app.utils.encoding import build_visitor_channel_id
from tests.test_im_access import records as records


@pytest.fixture
def fallback(records, monkeypatch):
    db, projects, staff, platforms, visitors = records
    engine = db.get_bind()
    for model in (VisitorSession, AIInteractionRun, AIExecutionAttempt):
        model.__table__.create(engine)
    visitor, platform = visitors[0], platforms[0]
    platform.ai_mode, platform.fallback_to_ai_timeout = "assist", 30
    platform.agent_id = uuid4()
    visitor.service_status = "active"
    visitor.service_mode, visitor.ai_disabled = None, None
    visitor.is_last_message_from_visitor = True
    visitor.last_message_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    visitor.last_client_msg_no = "original-customer-message"
    visitor.humanization_skill_name = "conversation-style"
    visitor.humanization_skill_enabled = False
    session = VisitorSession(
        project_id=platform.project_id, visitor_id=visitor.id,
        platform_id=platform.id, staff_id=staff[0].id, status="open",
    )
    db.add(session)
    db.commit()
    make_session = lambda: Session(engine)
    for module in (task, reply_service_mode, interactions):
        monkeypatch.setattr(module, "SessionLocal", make_session)
    monkeypatch.setattr(timed_ai_fallback, "SessionLocal", make_session)
    agent = {"id": str(platform.agent_id), "is_active": True}
    client = SimpleNamespace(
        get_agent=AsyncMock(return_value=agent),
        list_agents=AsyncMock(return_value={"data": [agent]}),
    )
    monkeypatch.setattr(employee_style, "ai_client", client)
    message = WuKongIMMessage.model_validate({
        "header": {"no_persist": False, "red_dot": True, "sync_once": False},
        "setting": 0, "message_id": 1, "message_seq": 1,
        "client_msg_no": visitor.last_client_msg_no,
        "from_uid": f"{visitor.id}-vtr",
        "channel_id": build_visitor_channel_id(visitor.id), "channel_type": 251,
        "timestamp": int(visitor.last_message_at.timestamp()),
        "payload": {"type": 1, "content": "查询商品材质"},
    })
    lookup = AsyncMock(return_value=message)
    monkeypatch.setattr(task.wukongim_client, "get_message_by_client_msg_no", lookup)
    publish = AsyncMock(return_value={"success": True, "content": "有依据的回答"})
    monkeypatch.setattr(task, "handle_ai_response_non_stream", publish)
    return SimpleNamespace(
        db=db, make_session=make_session, project=projects[0], staff=staff[0],
        platform=platform, visitor=visitor, session=session, agent=agent,
        client=client, message=message, lookup=lookup, publish=publish,
    )


def refresh(api):
    api.db.expire_all()
    return api.db.get(Visitor, api.visitor.id)


@pytest.mark.asyncio
async def test_timeout_can_publish_without_changing_assist_policy(fallback):
    api = fallback

    async def publish(**kwargs):
        reply_service_mode._require_auto_mode(kwargs["project_id"], kwargs["visitor_id"])
        return {"success": True, "content": "有依据的回答"}

    api.publish.side_effect = publish
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    assert visitor.is_last_message_from_ai and not visitor.is_last_message_from_visitor
    assert visitor.last_client_msg_no == api.publish.await_args.kwargs["client_msg_no"]
    assert visitor.service_mode is None and api.platform.ai_mode == "assist"
    call = api.publish.await_args.kwargs
    assert call["agent_id"] == str(api.platform.agent_id)
    assert call["humanization_skill_name"] is None
    assert call["knowledge_channel"] == "web"
    assert api.db.query(AIInteractionRun).one().status == "completed"
    api.lookup.assert_awaited_once_with(
        channel_id=build_visitor_channel_id(visitor.id), channel_type=251,
        client_msg_no="original-customer-message", raise_on_error=True,
    )
    with pytest.raises(ReplyStopped):
        reply_service_mode._require_auto_mode(str(api.project.id), str(visitor.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [False, True])
async def test_inactive_employee_never_starts_fallback(fallback, bound):
    api = fallback
    api.agent["is_active"] = False
    if not bound:
        api.platform.agent_id = None
        api.db.commit()
    await task.check_and_fallback_to_ai()
    api.publish.assert_not_awaited()
    assert refresh(api).last_client_msg_no == "original-customer-message"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["manual", "assist", "disabled", "closed", "retired", "not_due", "wechat_local"])
async def test_ineligible_customer_never_starts_lookup(fallback, fault):
    api = fallback
    if fault in ("manual", "assist"):
        api.visitor.service_mode = fault
    elif fault == "disabled":
        api.visitor.ai_disabled = True
    elif fault == "closed":
        api.visitor.service_status = "closed"
    elif fault == "retired":
        api.visitor.access_retired_at = datetime.now(timezone.utc)
    elif fault == "not_due":
        api.visitor.last_message_at = datetime.now(timezone.utc)
    else:
        api.platform.type = "wechat_local"
    api.db.commit()
    await task.check_and_fallback_to_ai()
    api.lookup.assert_not_awaited()
    api.publish.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["new_message", "manual", "closed", "staff", "platform"])
async def test_changed_source_after_lookup_is_not_replied_or_overwritten(fallback, fault):
    api = fallback

    async def lookup(**_kwargs):
        with api.make_session() as db:
            if fault == "new_message":
                db.get(Visitor, api.visitor.id).last_client_msg_no = "new-message"
            elif fault == "manual":
                db.get(Visitor, api.visitor.id).service_mode = "manual"
            elif fault == "closed":
                db.get(VisitorSession, api.session.id).status = "closed"
            elif fault == "staff":
                db.get(VisitorSession, api.session.id).staff_id = None
            else:
                from app.models import Platform
                db.get(Platform, api.platform.id).is_active = False
            db.commit()
        return api.message

    api.lookup.side_effect = lookup
    await task.check_and_fallback_to_ai()
    api.publish.assert_not_awaited()
    visitor = refresh(api)
    assert visitor.ai_fallback_retry_count == 0
    assert visitor.last_client_msg_no == (
        "new-message" if fault == "new_message" else "original-customer-message"
    )


@pytest.mark.asyncio
async def test_new_customer_message_during_generation_stops_publication(fallback):
    api = fallback

    async def publish(**kwargs):
        with api.make_session() as db:
            db.get(Visitor, api.visitor.id).last_client_msg_no = "new-message"
            db.commit()
        reply_service_mode._require_auto_mode(kwargs["project_id"], kwargs["visitor_id"])
        pytest.fail("stale reply was allowed to publish")

    api.publish.side_effect = publish
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    assert visitor.last_client_msg_no == "new-message"
    assert visitor.ai_fallback_retry_count == 0


@pytest.mark.asyncio
async def test_repeated_scanner_reuses_durable_source_identity(fallback):
    api = fallback
    entered, release = asyncio.Event(), asyncio.Event()

    async def publish(**_kwargs):
        entered.set()
        await release.wait()
        return {"success": True, "content": "有依据的回答"}

    api.publish.side_effect = publish
    first = asyncio.create_task(task.check_and_fallback_to_ai())
    await asyncio.wait_for(entered.wait(), timeout=3)
    try:
        await asyncio.wait_for(task.check_and_fallback_to_ai(), timeout=3)
        assert api.publish.await_count == 1
    finally:
        release.set()
        await first
    assert api.db.query(AIInteractionRun).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, 123, [], ""])
async def test_nontext_payload_does_not_reach_model(fallback, content):
    api = fallback
    api.message.payload["content"] = content
    await task.check_and_fallback_to_ai()
    api.publish.assert_not_awaited()
    assert refresh(api).ai_fallback_retry_count == task.MAX_AI_FALLBACK_RETRIES


@pytest.mark.asyncio
async def test_lookup_outage_keeps_retryable_source_without_private_logs(fallback, caplog):
    api = fallback
    api.lookup.side_effect = RuntimeError("private-lookup-body-and-token")
    await task.check_and_fallback_to_ai()
    assert refresh(api).ai_fallback_retry_count == 0
    assert "private-lookup-body-and-token" not in caplog.text
    api.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_handoff_is_not_recorded_as_an_ai_answer(fallback):
    api = fallback
    api.publish.return_value = {"success": True, "handoff": True, "content": ""}
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    assert visitor.last_client_msg_no == "original-customer-message"
    assert visitor.is_last_message_from_visitor
    assert visitor.ai_fallback_retry_count == task.MAX_AI_FALLBACK_RETRIES


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "channel", "sender", "source", "type"])
async def test_message_locator_cannot_replace_owned_text(fallback, fault):
    api = fallback
    if fault == "missing":
        api.lookup.return_value = None
    elif fault == "channel":
        api.message.channel_id = "foreign-vtr"
    elif fault == "sender":
        api.message.from_uid = "foreign-staff"
    elif fault == "source":
        api.message.client_msg_no = "different-message"
    else:
        api.message.payload["type"] = 2
    await task.check_and_fallback_to_ai()
    api.publish.assert_not_awaited()
    assert refresh(api).ai_fallback_retry_count == task.MAX_AI_FALLBACK_RETRIES


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_failed_or_uncertain_generation_is_not_automatically_replayed(fallback, caplog, failure):
    api = fallback
    if failure:
        api.publish.side_effect = RuntimeError("private-generation-body")
    else:
        api.publish.return_value = {"success": False, "error": "private-generation-body"}
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.publish.await_count == 1
    assert refresh(api).ai_fallback_retry_count == task.MAX_AI_FALLBACK_RETRIES
    row = api.db.query(AIInteractionRun).one()
    assert row.status == "failed" and row.error_message == "ai_processing_failed"
    assert "private-generation-body" not in caplog.text


@pytest.mark.asyncio
async def test_scoped_checks_reject_foreign_target_and_expire_after_await(fallback):
    api = fallback
    source = timed_ai_fallback.capture_source(api.db, api.platform, api.visitor)
    assert source is not None
    with timed_ai_fallback.fallback_scope(source):
        with pytest.raises(ReplyStopped):
            timed_ai_fallback.allows_timed_reply(api.db, str(uuid4()), str(api.visitor.id))
        other = api.db.query(Visitor).filter(Visitor.id != api.visitor.id).one()
        with pytest.raises(ReplyStopped):
            reply_service_mode._require_auto_mode(str(other.project_id), str(other.id))

        async def changed():
            with api.make_session() as db:
                db.get(Visitor, source.visitor_id).ai_disabled = True
                db.commit()
            return "must-not-release"

        with pytest.raises(ReplyStopped):
            await guarded_chat_call(changed)
    assert not timed_ai_fallback.allows_timed_reply(api.db, str(api.project.id), str(api.visitor.id))


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["normal", "new_message", "own_receipt"])
async def test_fallback_uses_actual_shared_publication_pipeline(fallback, monkeypatch, delivery):
    from app.core.config import settings
    from app.core.contextual_reply_config import ReviewedFAQ
    from app.schemas.agent_execution import AgentExecutionCheckReceipt, AgentExecutionReference
    from app.schemas.contextual_reply import ContextualReply
    from app.services import chat_service

    api = fallback
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    reference = AgentExecutionReference(
        project_id=api.project.id, agent_id=api.platform.agent_id, channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64,
    )

    async def capture(*_args):
        if delivery == "new_message":
            with api.make_session() as db:
                db.get(Visitor, api.visitor.id).last_client_msg_no = "new-message"
                db.commit()
        return reference.model_dump(mode="json")

    forward = AsyncMock()

    async def publish_event(**kwargs):
        if delivery == "own_receipt" and kwargs["event_type"] == "workflow_completed":
            with api.make_session() as db:
                visitor = db.get(Visitor, api.visitor.id)
                visitor.last_client_msg_no = kwargs["client_msg_no"]
                visitor.is_last_message_from_ai = True
                visitor.is_last_message_from_visitor = False
                visitor.last_message_at = datetime.now(timezone.utc)
                db.commit()

    forward.side_effect = publish_event
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    monkeypatch.setattr(chat_service.ai_client, "capture_agent_execution_reference", AsyncMock(side_effect=capture))
    monkeypatch.setattr(chat_service.ai_client, "verify_agent_execution", AsyncMock(return_value=AgentExecutionCheckReceipt(
        reference=reference, checked_at=datetime.now(timezone.utc),
    )))
    faq = ReviewedFAQ(
        question="查询商品材质", subject="商品", answer="已审核的商品材质回答",
        valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )

    async def pipeline(**kwargs):
        return await chat_service.handle_ai_response_non_stream(
            **kwargs, contextual_reply=ContextualReply(reviewed_faq=faq),
        )

    api.publish.side_effect = pipeline
    await task.check_and_fallback_to_ai()
    terminal = [call for call in forward.await_args_list if call.kwargs["event_type"] == "workflow_completed"]
    visitor = refresh(api)
    if delivery == "new_message":
        assert not terminal and visitor.last_client_msg_no == "new-message"
    else:
        assert len(terminal) == 1
        assert terminal[0].kwargs["event_data"]["data"]["final_content"] == faq.answer
        assert visitor.is_last_message_from_ai and not visitor.is_last_message_from_visitor
        assert api.db.query(AIInteractionRun).one().status == "completed"


@pytest.mark.asyncio
async def test_cancellation_keeps_source_stopped_across_another_scan(fallback):
    api = fallback
    entered = asyncio.Event()

    async def model(**_kwargs):
        entered.set()
        await asyncio.Event().wait()

    api.publish.side_effect = model
    invocation = asyncio.create_task(task.check_and_fallback_to_ai())
    await asyncio.wait_for(entered.wait(), timeout=3)
    invocation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await invocation
    await task.check_and_fallback_to_ai()
    assert api.publish.await_count == 1
    assert refresh(api).ai_fallback_retry_count == task.MAX_AI_FALLBACK_RETRIES
    assert api.db.query(AIInteractionRun).one().status == "failed"


@pytest.mark.asyncio
async def test_metadata_write_failure_retains_durable_claim(fallback, monkeypatch):
    api = fallback

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private-storage-outage")

    monkeypatch.setattr(task, "update_source", unavailable)
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.publish.await_count == 1
    assert api.db.query(AIInteractionRun).one().status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["no_staff", "disabled_staff", "foreign_staff", "foreign_session"])
async def test_current_assigned_staff_is_required(fallback, fault):
    api = fallback
    if fault == "no_staff":
        api.session.staff_id = None
    elif fault == "disabled_staff":
        api.staff.account_enabled = False
    elif fault == "foreign_staff":
        api.staff.project_id = uuid4()
    else:
        api.session.platform_id = uuid4()
    api.db.commit()
    await task.check_and_fallback_to_ai()
    api.lookup.assert_not_awaited()
    api.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_periodic_task_is_single_owned_and_stops_without_private_logs(monkeypatch, caplog):
    entered = asyncio.Event()

    async def scan():
        entered.set()
        raise RuntimeError("private-periodic-body")

    monkeypatch.setattr(task, "check_and_fallback_to_ai", scan)
    try:
        await task.start_auto_fallback_to_ai_task()
        first = task._auto_fallback_task
        await task.start_auto_fallback_to_ai_task()
        assert task._auto_fallback_task is first
        await asyncio.wait_for(entered.wait(), timeout=3)
    finally:
        await task.stop_auto_fallback_to_ai_task()
    assert first.cancelled() and task._auto_fallback_task is None
    await task.stop_auto_fallback_to_ai_task()
    assert "private-periodic-body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [None, "", 123, "x" * 256, "wecom-original-source"])
async def test_external_source_keeps_the_native_delivery_receipt_identity(fallback, original):
    api = fallback
    api.platform.type = "wecom"
    api.message.payload["extra"] = {"message_id": original}
    api.db.commit()
    await task.check_and_fallback_to_ai()
    if original == "wecom-original-source":
        api.publish.assert_awaited_once()
        assert api.db.query(AIInteractionRun).one().source_message_id == original
        assert api.publish.await_args.kwargs["knowledge_channel"] == "wecom_kf"
    else:
        api.publish.assert_not_awaited()
        assert api.db.query(AIInteractionRun).count() == 0


@pytest.mark.asyncio
async def test_already_completed_source_is_not_claimed_again(fallback):
    api = fallback
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    visitor.last_client_msg_no = "original-customer-message"
    visitor.is_last_message_from_visitor = True
    visitor.is_last_message_from_ai = False
    api.db.commit()
    await task.check_and_fallback_to_ai()
    assert api.publish.await_count == 1
    assert api.db.query(AIInteractionRun).count() == 1

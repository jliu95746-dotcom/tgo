"""Retry confirmed pre-publication failures without duplicate customer bubbles."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.contextual_reply_config import ReviewedFAQ
from app.models.ai_interaction_run import AIInteractionRun
from app.schemas.agent_execution import AgentExecutionCheckReceipt, AgentExecutionReference
from app.schemas.contextual_reply import ContextualReply
from app.services import ai_reply_control, chat_service
from app.services.ai_interaction_run_service import build_request_fingerprint
from app.tasks import auto_fallback_to_ai as task
from tests.test_auto_fallback_to_ai import fallback as fallback, refresh
from tests.test_im_access import records as records


@pytest.fixture
def retry_pipeline(fallback, monkeypatch):
    api = fallback
    monkeypatch.setattr(settings, "SAAS_ENABLED", False)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", False)
    monkeypatch.setattr(ai_reply_control, "PHASE_WAIT_SECONDS", 0.02)
    reference = AgentExecutionReference(
        project_id=api.project.id, agent_id=api.platform.agent_id, channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64,
    )
    monkeypatch.setattr(chat_service.ai_client, "capture_agent_execution_reference", AsyncMock(
        return_value=reference.model_dump(mode="json"),
    ))
    monkeypatch.setattr(chat_service.ai_client, "verify_agent_execution", AsyncMock(
        return_value=AgentExecutionCheckReceipt(reference=reference, checked_at=datetime.now(timezone.utc)),
    ))
    monkeypatch.setattr(chat_service.ai_client, "cancel_supervisor_run", AsyncMock(
        side_effect=lambda _project, run_id: {"run_id": run_id, "cancelled": False},
    ))
    api.anchor, api.events = AsyncMock(), AsyncMock()
    monkeypatch.setattr(chat_service.wukongim_client, "enabled", True)
    monkeypatch.setattr(chat_service.wukongim_client, "send_stream_message", api.anchor)
    monkeypatch.setattr(chat_service.wukongim_client, "send_stream_event", api.events)
    api.history = AsyncMock(side_effect=RuntimeError("private-history-failure"))
    monkeypatch.setattr(chat_service, "recent_customer_messages", api.history)
    api.faq = ReviewedFAQ(
        question="查询商品材质", subject="商品", answer="已审核的材质回答",
        valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    api.pipeline_mode, api.pipeline_count = "always_fail", 0

    async def pipeline(**kwargs):
        api.pipeline_count += 1
        succeeds = api.pipeline_mode == "success" or (
            api.pipeline_mode == "fail_then_success" and api.pipeline_count > 1
        )
        return await chat_service.handle_ai_response_non_stream(
            **kwargs, contextual_reply=ContextualReply(reviewed_faq=api.faq) if succeeds else None,
        )

    api.invoke = AsyncMock(side_effect=pipeline)
    monkeypatch.setattr(task, "handle_ai_response_non_stream", api.invoke)
    return api


@pytest.mark.asyncio
async def test_confirmed_failure_retries_three_times_with_one_final_notice(retry_pipeline, caplog):
    api = retry_pipeline
    for expected in (1, 2):
        await task.check_and_fallback_to_ai()
        assert refresh(api).ai_fallback_retry_count == expected
        assert api.db.query(AIInteractionRun).one().error_message == "ai_fallback_retryable"
        api.anchor.assert_not_awaited()
        api.events.assert_not_awaited()
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 3
    assert refresh(api).ai_fallback_retry_count == 3
    api.anchor.assert_awaited_once()
    assert [call.kwargs["event_type"] for call in api.events.await_args_list] == [
        "stream.error", "stream.close", "stream.finish",
    ]
    assert api.db.query(AIInteractionRun).count() == 1
    assert api.db.query(AIInteractionRun).one().status == "failed"
    assert "private-history-failure" not in caplog.text


@pytest.mark.asyncio
async def test_successful_retry_publishes_one_answer_and_resets_count(retry_pipeline):
    api = retry_pipeline
    api.pipeline_mode = "fail_then_success"
    await task.check_and_fallback_to_ai()
    assert refresh(api).ai_fallback_retry_count == 1
    api.anchor.assert_not_awaited()
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    assert visitor.ai_fallback_retry_count == 0 and visitor.is_last_message_from_ai
    assert not visitor.is_last_message_from_visitor
    assert api.invoke.await_count == 2
    api.anchor.assert_awaited_once()
    assert [call.kwargs["event_type"] for call in api.events.await_args_list] == [
        "stream.delta", "stream.close", "stream.finish",
    ]
    assert api.events.await_args_list[0].kwargs["payload"]["delta"] == api.faq.answer
    assert api.db.query(AIInteractionRun).one().status == "completed"


@pytest.mark.asyncio
async def test_unknown_anchor_delivery_is_not_replayed(retry_pipeline):
    api = retry_pipeline
    api.pipeline_mode = "success"
    api.anchor.side_effect = RuntimeError("private-delivery-outage")
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1
    assert refresh(api).ai_fallback_retry_count == 3
    api.anchor.assert_awaited_once()
    assert not any(call.kwargs["event_type"] == "stream.delta" for call in api.events.await_args_list)
    assert api.db.query(AIInteractionRun).one().status == "failed"


@pytest.mark.asyncio
async def test_returned_retry_flag_cannot_authorize_another_generation(retry_pipeline):
    api = retry_pipeline
    api.invoke.side_effect = None
    api.invoke.return_value = {"success": False, "retry_allowed": True, "published": False}
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1 and refresh(api).ai_fallback_retry_count == 3


@pytest.mark.asyncio
async def test_unconfirmed_upstream_shutdown_prevents_retry(retry_pipeline, monkeypatch):
    api = retry_pipeline
    api.history.side_effect, api.history.return_value = None, []

    calls = []

    async def unconfirmed(**_kwargs):
        calls.append(True)
        yield "agent_execution_started", {"data": {"execution_id": str(uuid4())}}
        raise RuntimeError("private-upstream-interruption")

    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", unconfirmed)
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1 and refresh(api).ai_fallback_retry_count == 3
    assert len(calls) == 1
    assert api.db.query(AIInteractionRun).one().status == "failed"


@pytest.mark.asyncio
async def test_reception_change_between_retries_keeps_human_mode(retry_pipeline):
    api = retry_pipeline
    await task.check_and_fallback_to_ai()
    visitor = refresh(api)
    assert visitor.ai_fallback_retry_count == 1
    visitor.service_mode, visitor.ai_disabled = "manual", True
    api.db.commit()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1
    api.anchor.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_state_write_failure_keeps_claim_non_replayable(retry_pipeline, monkeypatch):
    api = retry_pipeline

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("private-retry-state-outage")

    monkeypatch.setattr(task, "update_source", unavailable)
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1
    assert api.db.query(AIInteractionRun).one().status == "running"
    api.anchor.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "foreign", "unavailable", "cancelled"])
async def test_unconfirmed_or_cancelled_control_never_grants_retry_or_notice(retry_pipeline, monkeypatch, fault):
    from app.services.run_registry import RegistryUnavailable

    api = retry_pipeline
    registry = ai_reply_control.run_registry
    finish = registry.finish

    async def uncertain(identity, status, reason=None, *, preserve_cancel=False):
        if fault == "unavailable":
            raise RegistryUnavailable("private-control-outage")
        if fault == "cancelled":
            await registry.request_cancel(identity)
            return await finish(identity, status, reason, preserve_cancel=preserve_cancel)
        saved = await finish(identity, status, reason, preserve_cancel=preserve_cancel)
        return None if fault == "missing" else saved.model_copy(update={"generation": uuid4()})

    monkeypatch.setattr(registry, "finish", uncertain)
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1
    assert refresh(api).ai_fallback_retry_count == 3
    api.anchor.assert_not_awaited()
    api.events.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["channel", "sender", "client", "ungranted_final"])
async def test_bad_publication_coordinates_cannot_become_retry_permission(retry_pipeline, monkeypatch, fault):
    api = retry_pipeline
    forward = chat_service.forward_ai_event_to_wukongim

    async def wrong_target(**kwargs):
        if kwargs["event_type"] == "agent_execution_started":
            if fault == "channel":
                kwargs["channel_id"] = "foreign-vtr"
            elif fault == "sender":
                kwargs["from_uid"] = "foreign-staff"
            elif fault == "client":
                kwargs["client_msg_no"] = "different-reply"
            else:
                kwargs["event_type"] = "workflow_completed"
        return await forward(**kwargs)

    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", wrong_target)
    await task.check_and_fallback_to_ai()
    await task.check_and_fallback_to_ai()
    assert api.invoke.await_count == 1
    assert refresh(api).ai_fallback_retry_count == 3
    api.anchor.assert_not_awaited()
    api.events.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_failed_run_without_server_retry_receipt_is_not_replayed(retry_pipeline):
    api = retry_pipeline
    api.db.add(AIInteractionRun(
        project_id=api.project.id, platform_id=api.platform.id,
        visitor_id=api.visitor.id, channel_id=api.message.channel_id,
        channel_type=251, source_message_id=api.message.client_msg_no,
        request_fingerprint=build_request_fingerprint(
            visitor_id=api.visitor.id, message="查询商品材质", message_type=1,
        ),
        response_client_msg_no="legacy-uncertain-response",
        status="failed", error_message="ai_processing_failed",
    ))
    api.db.commit()
    await task.check_and_fallback_to_ai()
    api.invoke.assert_not_awaited()
    assert refresh(api).ai_fallback_retry_count == 3
    assert api.db.query(AIInteractionRun).one().response_client_msg_no == "legacy-uncertain-response"


def test_model_error_text_cannot_be_a_durable_retry_receipt():
    from app.services.ai_interaction_run_service import safe_ai_interaction_failure_code

    assert safe_ai_interaction_failure_code("ai_fallback_retryable") == "ai_processing_failed"


@pytest.mark.parametrize("message", [None, "ai_fallback_retryable", "business_context_unavailable"])
def test_retry_finish_requires_the_explicit_failed_attempt_path(message):
    from app.services.ai_interaction_run_service import mark_ai_interaction_finished

    with pytest.raises(ValueError, match="requires a failed attempt"):
        mark_ai_interaction_finished(
            uuid4(), response_client_msg_no="fixture", error_message=message,
            retryable_fallback=True,
        )

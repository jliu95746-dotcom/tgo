"""A completed model result is never sufficient to authorize publication."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_execution import AgentExecutionCheckReceipt, AgentExecutionReference
from app.services.agent_execution_client import verify
from app.services.agent_execution_publication import ensure_execution_can_publish


def reference():
    return AgentExecutionReference(project_id=uuid4(), agent_id=uuid4(), channel="web",
        configuration_fingerprint="a" * 64, policy_revision="b" * 64)


@pytest.mark.asyncio
async def test_exact_producer_identity_is_rechecked_without_selecting_a_new_default():
    ref = reference()
    owner = AsyncMock()
    owner.verify_agent_execution.return_value = AgentExecutionCheckReceipt(reference=ref, checked_at=datetime.now(UTC))
    await ensure_execution_can_publish(owner, ref.model_dump(mode="json"), project_id=str(ref.project_id), agent_id=None, channel="web")
    owner.verify_agent_execution.assert_awaited_once_with(ref)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "project", "agent", "channel", "extra"])
async def test_malformed_or_foreign_producer_never_reaches_publication_owner(fault):
    ref = reference()
    body = ref.model_dump(mode="json")
    if fault == "missing":
        body = None
    elif fault == "extra":
        body["can_enable"] = True
    else:
        body[f"{fault}_id" if fault != "channel" else "channel"] = "internal" if fault == "channel" else str(uuid4())
    owner = AsyncMock()
    with pytest.raises(HTTPException):
        await ensure_execution_can_publish(owner, body, project_id=str(ref.project_id), agent_id=str(ref.agent_id), channel="web")
    owner.verify_agent_execution.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "old", "foreign", "missing_scope", "grant", "denied", "business_revision"])
async def test_private_transport_accepts_only_explicit_current_matching_receipt(fault):
    ref = reference()
    body = AgentExecutionCheckReceipt(reference=ref, checked_at=datetime.now(UTC)).model_dump(mode="json")
    if fault == "old":
        body["checked_at"] = (datetime.now(UTC) - timedelta(minutes=2)).isoformat()
    elif fault == "foreign":
        body["reference"]["agent_id"] = str(uuid4())
    elif fault == "missing_scope":
        body.pop("scope")
    elif fault == "grant":
        body["can_enable"] = True
    elif fault == "denied":
        body = {"credential": "private-upstream-token"}
    elif fault == "business_revision":
        body["reference"]["business_evidence_revision"] = "c" * 64
    client = AsyncMock()
    client._execution_headers = lambda project, channel: {"X-Project-Id": project, "X-Execution-Channel": channel}
    client._make_request.return_value = httpx.Response(409 if fault == "denied" else 200, json=body)
    if fault is None:
        assert (await verify(client, ref)).reference == ref
        assert client._make_request.call_args.kwargs["json_data"] == ref.model_dump(mode="json")
    else:
        with pytest.raises(HTTPException) as failure:
            await verify(client, ref)
        assert "private-upstream-token" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_revocation_after_rewrite_cannot_reach_stream_or_persisted_publication(monkeypatch, stream):
    from app.services import chat_service
    ref = reference()

    async def events(**kwargs):
        yield "agent_response_complete", {"data": {"success": True, "final_content": "factual answer",
            "execution_reference": ref.model_dump(mode="json")}}
        yield "workflow_completed", {"data": {"success": True}}

    verify_owner = AsyncMock(side_effect=HTTPException(409, "scope changed"))
    monkeypatch.setattr(chat_service.ai_client, "run_supervisor_agent_stream", events)
    monkeypatch.setattr(chat_service.ai_client, "verify_agent_execution", verify_owner)
    monkeypatch.setattr(chat_service, "ensure_customer_auto_reply", AsyncMock())
    monkeypatch.setattr(chat_service, "recent_customer_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_service, "rewrite_assist_draft", AsyncMock(return_value="must not publish"))
    forward, publication = AsyncMock(), AsyncMock()
    monkeypatch.setattr(chat_service, "forward_ai_event_to_wukongim", forward)
    monkeypatch.setattr(chat_service, "begin_reply_publication", publication)
    args = dict(project_id=str(ref.project_id), message="test", channel_id="owned", channel_type=251,
        client_msg_no="owned", from_uid="owned", knowledge_channel="web")
    if stream:
        output = [event async for event in chat_service.process_ai_stream_to_wukongim(user_id="owned", **args)]
        assert not any(event["event_type"] == "agent_content_chunk" for event in output)
    else:
        output = await chat_service.handle_ai_response_non_stream(visitor_id="owned", **args)
        assert not output.get("content")
    verify_owner.assert_awaited_once_with(ref)
    publication.assert_not_awaited()
    assert not any(call.kwargs["event_type"] == "workflow_completed" for call in forward.await_args_list)

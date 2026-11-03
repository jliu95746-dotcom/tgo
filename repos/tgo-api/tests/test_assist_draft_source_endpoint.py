"""The staff draft endpoint stores IM source proof before returning a draft."""

from datetime import datetime, timezone
import httpx
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import chat
from app.models import Visitor
from app.schemas.chat import AssistDraftRequest, AssistDraftResponse
from app.schemas.shadow_review import ShadowSourceProof
from app.services import (
    assist_draft_record,
    ai_usage_workbench,
    shadow_source_proof,
)
from app.services import staff_conversation_scope
from app.services.ai_client import AIServiceClient


@pytest.mark.asyncio
async def test_assist_endpoint_persists_proof_and_replay_skips_second_lookup(
    monkeypatch: pytest.MonkeyPatch,
):
    project_id, visitor_id, staff_id, draft_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    visitor = SimpleNamespace(
        id=visitor_id, platform=SimpleNamespace(id=uuid4())
    )
    staff = SimpleNamespace(id=staff_id, project_id=project_id, role="admin")
    db = MagicMock()

    def query(model):
        result = MagicMock()
        result.options.return_value = result
        result.filter.return_value = result
        result.first.return_value = visitor if model is Visitor else object()
        return result

    db.query.side_effect = query
    monkeypatch.setattr(
        staff_conversation_scope, "require_owned_visitor", lambda *_: None
    )
    monkeypatch.setattr(
        chat.chat_service, "resolve_service_mode", lambda *_: "assist"
    )
    monkeypatch.setattr(
        chat, "_resolve_platform_agent_kwargs", AsyncMock(return_value={})
    )
    proof = ShadowSourceProof(
        status="verified", checked_at=datetime.now(timezone.utc)
    )
    verify = AsyncMock(return_value=proof)
    monkeypatch.setattr(shadow_source_proof, "verify_shadow_source", verify)
    generation = AsyncMock(
        return_value=AssistDraftResponse(draft="可放 13 英寸电脑")
    )
    monkeypatch.setattr(ai_usage_workbench, "generate_workbench", generation)
    saved = SimpleNamespace(
        id=draft_id,
        response_data={"draft": "可放 13 英寸电脑"},
    )
    save = MagicMock(return_value=saved)
    get = MagicMock(return_value=None)
    monkeypatch.setattr(assist_draft_record, "save_assist_draft", save)
    monkeypatch.setattr(assist_draft_record, "get_assist_draft", get)
    request = AssistDraftRequest(
        visitor_id=visitor_id,
        request_id=uuid4(),
        source_message_id="external-42",
        source_client_msg_no="im-client-42",
        customer_message="这款包多大？",
    )
    first = await chat.generate_assist_draft(request, db, staff)
    assert first.draft_id == draft_id
    assert first.draft == "可放 13 英寸电脑"
    assert save.call_args.kwargs["source_proof"] is proof
    assert verify.call_args.kwargs["source_client_msg_no"] == "im-client-42"

    get.return_value = saved
    second = await chat.generate_assist_draft(request, db, staff)
    assert second.draft_id == draft_id
    assert verify.await_count == 1
    assert generation.await_count == 1


@pytest.mark.asyncio
async def test_assist_endpoint_persists_model_trace_from_actual_generation(
    monkeypatch: pytest.MonkeyPatch,
):
    project_id, visitor_id, staff_id, draft_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    visitor = SimpleNamespace(
        id=visitor_id, platform=SimpleNamespace(id=uuid4())
    )
    staff = SimpleNamespace(id=staff_id, project_id=project_id, role="admin")
    db = MagicMock()

    def query(model):
        result = MagicMock()
        result.options.return_value = result
        result.filter.return_value = result
        result.first.return_value = visitor if model is Visitor else object()
        return result

    db.query.side_effect = query
    monkeypatch.setattr(
        staff_conversation_scope, "require_owned_visitor", lambda *_: None
    )
    monkeypatch.setattr(
        chat.chat_service, "resolve_service_mode", lambda *_: "assist"
    )
    monkeypatch.setattr(
        chat, "_resolve_platform_agent_kwargs", AsyncMock(return_value={})
    )
    monkeypatch.setattr(
        chat, "recent_customer_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        chat, "rewrite_assist_draft", AsyncMock(return_value="最终回复")
    )
    monkeypatch.setattr(
        shadow_source_proof,
        "verify_shadow_source",
        AsyncMock(
            return_value=ShadowSourceProof(
                status="unverified", checked_at=datetime.now(timezone.utc)
            )
        ),
    )
    monkeypatch.setattr(
        assist_draft_record, "get_assist_draft", MagicMock(return_value=None)
    )

    async def run_workbench(
        _project, _key, _fingerprint, generate, _response_type
    ):
        return await generate()

    monkeypatch.setattr(
        ai_usage_workbench, "generate_workbench", run_workbench
    )
    ai_response = httpx.Response(
        200,
        json={
            "success": True,
            "content": "业务初稿",
            "metadata": {
                "model_name": "openai:gpt-4o",
                "configured_prompt_sha256": "b" * 64,
            },
        },
        request=httpx.Request("POST", "http://test/agents/run"),
    )
    monkeypatch.setattr(
        AIServiceClient, "_make_request", AsyncMock(return_value=ai_response)
    )

    def save_draft(*_args, **kwargs):
        return SimpleNamespace(
            id=draft_id,
            response_data=kwargs["response"].model_dump(
                mode="json", exclude={"draft_id"}
            ),
        )

    monkeypatch.setattr(assist_draft_record, "save_assist_draft", save_draft)
    result = await chat.generate_assist_draft(
        AssistDraftRequest(
            visitor_id=visitor_id,
            customer_message="这款包多大？",
            source_message_id="source-1",
        ),
        db,
        staff,
    )
    assert result.draft_id == draft_id
    assert result.generation_trace[0].phase == "fact_answer"
    assert result.generation_trace[0].model_name == "openai:gpt-4o"
    assert result.generation_trace[0].configured_prompt_sha256 == "b" * 64

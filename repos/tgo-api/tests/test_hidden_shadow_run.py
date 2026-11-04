"""A hidden WeCom candidate is generated once and never sent to the customer."""

from datetime import datetime, timezone
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.hidden_shadow_run import HiddenShadowRun
from app.core.exceptions import TGOAPIException
from app.schemas.shadow_review import ShadowSourceProof
from app.services.hidden_shadow_run import HiddenShadowRunService


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    HiddenShadowRun.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def verified(message: str) -> ShadowSourceProof:
    return ShadowSourceProof(
        status="verified", checked_at=datetime.now(timezone.utc),
        channel_id="visitor-vtr", channel_type=251,
        client_msg_no="customer-message-1", message_id="101",
        message_seq=12, message_timestamp=1_797_000_000,
        content_sha256=sha256(message.strip().encode()).hexdigest(),
    )


@pytest.mark.asyncio
async def test_verified_wecom_message_generates_one_private_knowledge_candidate(
    db, monkeypatch,
):
    project_id, platform_id, visitor_id, agent_id = (uuid4() for _ in range(4))
    monkeypatch.setattr(
        "app.services.hidden_shadow_run.verify_shadow_source",
        AsyncMock(return_value=verified("包是什么材质？")),
    )
    ai = AsyncMock()
    ai.run_supervisor_agent.return_value = {
        "content": "这款包采用牛皮。",
        "execution_id": str(uuid4()),
        "metadata": {
            "model_name": "test-model",
            "configured_prompt_sha256": "f" * 64,
        },
        "knowledge_evidence": {
            "status": "matched", "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "project_id": str(project_id), "channel": "wecom_kf",
            "documents": [{
                "collection_id": "collection-1", "document_id": "document-1",
                "content": "这款包采用牛皮。",
            }], "tool_results": [],
        },
    }
    service = HiddenShadowRunService(db, ai_client=ai, im_client=AsyncMock())

    first = await service.generate(
        project_id=project_id, platform_id=platform_id,
        visitor_id=visitor_id, agent_id=agent_id,
        source_message_id="customer-message-1", customer_message="包是什么材质？",
    )
    second = await service.generate(
        project_id=project_id, platform_id=platform_id,
        visitor_id=visitor_id, agent_id=agent_id,
        source_message_id="customer-message-1", customer_message="包是什么材质？",
    )

    assert first is not None and first.status == "ready"
    assert first.answer == "这款包采用牛皮。"
    assert first.knowledge_evidence_sha256 is not None
    assert first.model_name == "test-model"
    assert first.configured_prompt_sha256 == "f" * 64
    assert first.ai_execution_id is not None
    assert second is not None and second.id == first.id
    assert len(db.scalars(select(HiddenShadowRun)).all()) == 1
    ai.run_supervisor_agent.assert_awaited_once()
    kwargs = ai.run_supervisor_agent.await_args.kwargs
    assert kwargs["knowledge_only"] is True
    assert kwargs["require_current_knowledge"] is True
    assert kwargs["enable_memory"] is False
    assert kwargs["knowledge_channel"] == "wecom_kf"
    assert "business_authorization" not in kwargs
    assert "reply_phase" not in kwargs


@pytest.mark.asyncio
async def test_unverified_source_and_missing_knowledge_never_create_ready_answer(
    db, monkeypatch,
):
    project_id, platform_id, visitor_id = (uuid4() for _ in range(3))
    source = AsyncMock(return_value=ShadowSourceProof(
        status="unavailable", checked_at=datetime.now(timezone.utc),
    ))
    monkeypatch.setattr("app.services.hidden_shadow_run.verify_shadow_source", source)
    ai = AsyncMock()
    service = HiddenShadowRunService(db, ai_client=ai, im_client=AsyncMock())
    skipped = await service.generate(
        project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
        agent_id=None, source_message_id="customer-message-1",
        customer_message="退货条件？",
    )
    assert skipped is None
    assert db.scalars(select(HiddenShadowRun)).all() == []
    ai.run_supervisor_agent.assert_not_awaited()

    source.return_value = verified("退货条件？")
    ai.run_supervisor_agent.return_value = {
        "content": "可能支持退货。",
        "knowledge_evidence": {
            "status": "no_match", "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "project_id": str(project_id), "channel": "wecom_kf",
            "documents": [], "tool_results": [],
        },
    }
    blocked = await service.generate(
        project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
        agent_id=None, source_message_id="customer-message-1",
        customer_message="退货条件？",
    )
    assert blocked is not None and blocked.status == "blocked"
    assert blocked.answer is None


@pytest.mark.asyncio
async def test_source_conflict_does_not_replace_existing_candidate(
    db, monkeypatch,
):
    project_id, platform_id, visitor_id = (uuid4() for _ in range(3))
    source = AsyncMock(return_value=verified("原消息"))
    monkeypatch.setattr("app.services.hidden_shadow_run.verify_shadow_source", source)
    ai = AsyncMock(return_value={})
    ai.run_supervisor_agent.return_value = {
        "content": "答复", "knowledge_evidence": {
            "status": "matched", "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "project_id": str(project_id), "channel": "wecom_kf",
            "documents": [{"collection_id": "c", "document_id": "d", "content": "答复"}],
            "tool_results": [],
        },
    }
    service = HiddenShadowRunService(db, ai_client=ai, im_client=AsyncMock())
    await service.generate(
        project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
        agent_id=None, source_message_id="customer-message-1",
        customer_message="原消息",
    )
    source.return_value = verified("篡改后的消息")

    with pytest.raises(TGOAPIException) as exc:
        await service.generate(
            project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
            agent_id=None, source_message_id="customer-message-1",
            customer_message="篡改后的消息",
        )
    assert exc.value.code == "HIDDEN_SHADOW_SOURCE_CONFLICT"
    ai.run_supervisor_agent.assert_awaited_once()
    assert db.scalar(select(HiddenShadowRun)).answer == "答复"


@pytest.mark.asyncio
async def test_ai_failure_keeps_private_run_retryable(db, monkeypatch):
    project_id, platform_id, visitor_id = (uuid4() for _ in range(3))
    monkeypatch.setattr(
        "app.services.hidden_shadow_run.verify_shadow_source",
        AsyncMock(return_value=verified("原消息")),
    )
    ai = AsyncMock()
    ai.run_supervisor_agent.side_effect = RuntimeError("private provider diagnostic")
    service = HiddenShadowRunService(db, ai_client=ai, im_client=AsyncMock())

    for attempt in range(3):
        row = await service.generate(
            project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
            agent_id=None, source_message_id="customer-message-1",
            customer_message="原消息",
        )
        assert row is not None and row.status == "failed"
        assert row.attempt_count == attempt + 1
        assert row.answer is None
        assert row.failure_code == "AI_RUN_FAILED"
        assert row.knowledge_evidence_sha256 is None
    await service.generate(
        project_id=project_id, platform_id=platform_id, visitor_id=visitor_id,
        agent_id=None, source_message_id="customer-message-1",
        customer_message="原消息",
    )
    assert ai.run_supervisor_agent.await_count == 3

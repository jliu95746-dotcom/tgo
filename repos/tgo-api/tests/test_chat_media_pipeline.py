"""Database-backed recognition/retry/classification flow without customer sends."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import TextClause

from app.models import (
    MediaAnalysisResult,
    MessageIntentResult,
    Platform,
    ProjectAIConfig,
    Visitor,
)
from app.services import chat_media_analysis as pipeline
from app.services.chat_media_service import MediaInputError
from tests import test_chat_media_input
from tests.test_chat_media_analysis import recognition

media = test_chat_media_input.media


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(_type, compiler, **kwargs):
    return "JSON"


@compiles(TextClause, "sqlite")
def compile_jsonb_default_sqlite(element, compiler, **kwargs):
    return compiler.visit_textclause(element, **kwargs).replace("::jsonb", "")


@pytest.fixture
def prepared(media, monkeypatch):
    db, file, request, storage, content = media
    engine = db.get_bind()
    for model in (
        Platform,
        Visitor,
        ProjectAIConfig,
        MediaAnalysisResult,
        MessageIntentResult,
    ):
        model.__table__.create(engine)
    db.add(
        Platform(
            id=request.platform_id,
            project_id=request.project_id,
            type="website",
            name="owned",
            api_key="owned-fixture-platform",
            is_active=True,
        )
    )
    db.add(
        Visitor(
            id=request.visitor_id,
            project_id=request.project_id,
            platform_id=request.platform_id,
            platform_open_id="owned",
            name="owned",
        )
    )
    db.add(
        ProjectAIConfig(
            project_id=request.project_id,
            default_chat_provider_id=uuid4(),
            default_chat_model="owned-classifier",
        )
    )
    db.commit()
    payload = recognition()
    payload["media_id"] = str(file.id)
    client = type("Client", (), {})()
    client.analyze_media = AsyncMock(return_value=payload)
    client.classify_intent = AsyncMock(
        return_value={
            "intent": "product_inquiry",
            "confidence": 0.99,
            "entities": {},
            "risk_level": "low",
            "recommended_route": "auto_reply",
            "need_human": False,
            "taxonomy_version": "v1",
            "routing_reason": "high_confidence_faq",
            "classification_source": "model",
        }
    )
    monkeypatch.setattr(pipeline, "SessionLocal", lambda: Session(engine))
    monkeypatch.setattr(pipeline, "AIServiceClient", lambda: client)
    monkeypatch.setattr("app.services.chat_media_service.get_storage", lambda: storage)
    return db, request, file, client


@pytest.mark.asyncio
async def test_recognition_and_intent_are_persisted_and_reused(prepared):
    db, request, file, client = prepared
    first = await pipeline.prepare_chat_media(request)
    second = await pipeline.prepare_chat_media(request)
    assert first == second and first.disable_tools is False
    assert client.analyze_media.await_count == client.classify_intent.await_count == 1
    classified = client.classify_intent.await_args.kwargs["classification_input"]
    assert (
        classified["ocr_text"]
        and classified["vlm_text"]
        and "user_text" not in classified
    )
    assert (
        db.query(MediaAnalysisResult).count()
        == db.query(MessageIntentResult).count()
        == 1
    )
    row = db.query(MediaAnalysisResult).one()
    assert row.normalized_text_is_untrusted and row.can_continue
    assert db.query(MessageIntentResult).one().media_analysis_result_id == row.id


@pytest.mark.asyncio
async def test_failed_recognition_can_be_retried_after_model_configuration(prepared):
    db, request, file, client = prepared
    failed = recognition(complete=False)
    failed["media_id"] = str(file.id)
    healthy = client.analyze_media.return_value
    client.analyze_media.side_effect = [failed, healthy]
    with pytest.raises(MediaInputError):
        await pipeline.prepare_chat_media(request)
    client.classify_intent.assert_not_awaited()
    assert db.query(MediaAnalysisResult).one().status == "failed"
    result = await pipeline.prepare_chat_media(request)
    assert result.customer_message and db.query(MediaAnalysisResult).count() == 1
    db.expire_all()
    assert db.query(MediaAnalysisResult).one().status == "completed"
    assert client.analyze_media.await_count == 2


@pytest.mark.asyncio
async def test_media_identifiers_require_confirmation_with_tools_disabled(prepared):
    _, request, _, client = prepared
    client.classify_intent.return_value.update(
        intent="order_query",
        recommended_route="clarify",
        routing_reason="untrusted_media_confirmation",
    )
    reply = await pipeline.prepare_chat_media(request)
    assert reply.disable_tools is True and "不查询订单" in reply.system_context


@pytest.mark.asyncio
async def test_high_risk_media_stops_automatic_reply_but_allows_staff_confirmation_draft(
    prepared,
):
    _, request, _, client = prepared
    client.classify_intent.return_value.update(
        intent="payment_issue",
        recommended_route="human_handoff",
        need_human=True,
        risk_level="high",
        routing_reason="high_risk",
    )
    with pytest.raises(MediaInputError, match="人工确认"):
        await pipeline.prepare_chat_media(request)
    staff = await pipeline.prepare_chat_media(request, for_assist=True)
    assert staff.disable_tools is True

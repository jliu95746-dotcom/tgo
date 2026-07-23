"""Staff-authenticated message-analysis read contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.message_analysis import require_message_analysis_read
from app.main import app
from app.models import MediaAnalysisResult, MessageIntentResult, Staff
from app.schemas.message_analysis import StaffMessageAnalysisBatchRequest
from app.services.message_analysis_service import (
    MessageAnalysisLookupKey,
    MessageAnalysisService,
)

ModelT = TypeVar("ModelT")


class _FakeBatchQuery:
    def __init__(
        self,
        session: _FakeBatchSession,
        model: type[object],
    ) -> None:
        self._session = session
        self._model = model

    def filter(self, *_criteria: object) -> _FakeBatchQuery:
        return self

    def all(self) -> list[object]:
        if self._model is MediaAnalysisResult:
            return list(self._session.media_results)
        if self._model is MessageIntentResult:
            return list(self._session.intent_results)
        raise AssertionError(f"Unexpected model query: {self._model}")


class _FakeBatchSession:
    def __init__(
        self,
        *,
        media_results: tuple[MediaAnalysisResult, ...] = (),
        intent_results: tuple[MessageIntentResult, ...] = (),
    ) -> None:
        self.media_results = media_results
        self.intent_results = intent_results

    def query(self, model: type[ModelT]) -> _FakeBatchQuery:
        return _FakeBatchQuery(self, model)


def _media_result(
    *,
    project_id: UUID,
    visitor_id: UUID,
    source_message_id: str,
) -> MediaAnalysisResult:
    now = datetime.now(timezone.utc)
    return MediaAnalysisResult(
        id=uuid4(),
        project_id=project_id,
        platform_id=uuid4(),
        visitor_id=visitor_id,
        source_message_id=source_message_id,
        source_media_record_id=uuid4(),
        media_type="image",
        media_sha256="a" * 64,
        mime_type="image/jpeg",
        status="completed",
        normalized_text="物流运输中",
        normalized_text_is_untrusted=True,
        sensitive_data_categories=[],
        transcript=None,
        ocr_text="物流运输中",
        vision_summary="物流截图",
        stages=[
            {
                "capability": "ocr",
                "status": "completed",
                "provider_name": "test-ocr",
                "text": "物流运输中",
                "confidence": 0.98,
                "model_version": "ocr-v1",
                "error": None,
                "text_is_untrusted": True,
                "sensitive_data_categories": [],
            },
            {
                "capability": "vlm",
                "status": "completed",
                "provider_name": "test-vlm",
                "text": "物流截图",
                "confidence": 0.95,
                "model_version": "vlm-v1",
                "error": None,
                "text_is_untrusted": True,
                "sensitive_data_categories": [],
            },
        ],
        can_continue=True,
        requires_handoff=False,
        fallback_message=None,
        pipeline_version="multimodal-v1",
        input_fingerprint="media-fingerprint",
        request_id="request-media",
        created_at=now,
        updated_at=now,
    )


def _intent_result(
    *,
    project_id: UUID,
    visitor_id: UUID,
    source_message_id: str,
) -> MessageIntentResult:
    now = datetime.now(timezone.utc)
    return MessageIntentResult(
        id=uuid4(),
        project_id=project_id,
        platform_id=uuid4(),
        visitor_id=visitor_id,
        source_message_id=source_message_id,
        media_analysis_result_id=None,
        intent="logistics_query",
        confidence=0.93,
        entities={
            "order_no": "ORDER-001",
            "product_name": None,
            "sku": None,
            "logistics_no": None,
            "payment_reference": None,
            "issue_summary": None,
        },
        risk_level="low",
        recommended_route="read_only_tool",
        need_human=False,
        taxonomy_version="v1",
        routing_reason="high_confidence_read_only",
        classification_source="model",
        classifier_version="classifier-v1",
        policy_version="policy-v1",
        input_fingerprint="intent-fingerprint",
        request_id="request-intent",
        created_at=now,
        updated_at=now,
    )


def _batch_payload(visitor_id: UUID) -> dict[str, object]:
    return {
        "messages": [
            {
                "channel_id": f"{visitor_id}-vtr",
                "source_message_id": "wecom-message-1",
            }
        ]
    }


def test_staff_batch_schema_rejects_duplicate_message_keys() -> None:
    visitor_id = uuid4()
    item = _batch_payload(visitor_id)["messages"][0]  # type: ignore[index]

    with pytest.raises(ValidationError):
        StaffMessageAnalysisBatchRequest.model_validate(
            {"messages": [item, item]}
        )


def test_staff_batch_read_filters_unrequested_and_cross_tenant_rows() -> None:
    project_id = uuid4()
    other_project_id = uuid4()
    visitor_id = uuid4()
    requested_media = _media_result(
        project_id=project_id,
        visitor_id=visitor_id,
        source_message_id="wecom-message-1",
    )
    cross_tenant_intent = _intent_result(
        project_id=other_project_id,
        visitor_id=visitor_id,
        source_message_id="wecom-message-1",
    )
    unrequested_intent = _intent_result(
        project_id=project_id,
        visitor_id=visitor_id,
        source_message_id="wecom-message-2",
    )
    service = MessageAnalysisService(  # type: ignore[arg-type]
        _FakeBatchSession(
            media_results=(requested_media,),
            intent_results=(cross_tenant_intent, unrequested_intent),
        )
    )

    results = service.get_combined_results_for_project(
        project_id=project_id,
        keys=(
            MessageAnalysisLookupKey(
                visitor_id=visitor_id,
                source_message_id="wecom-message-1",
            ),
        ),
    )

    assert len(results) == 1
    assert results[0].media is requested_media
    assert results[0].intent is None


def test_staff_batch_api_uses_jwt_project_and_returns_available_items(
    client: object,
    db_override: object,
) -> None:
    project_id = uuid4()
    visitor_id = uuid4()
    current_user = Staff(
        id=uuid4(),
        project_id=project_id,
        username="staff-reader",
        password_hash="test-only",
        name="Staff Reader",
        role="user",
    )
    db_override.session = _FakeBatchSession(  # type: ignore[attr-defined]
        media_results=(
            _media_result(
                project_id=project_id,
                visitor_id=visitor_id,
                source_message_id="wecom-message-1",
            ),
        ),
        intent_results=(
            _intent_result(
                project_id=project_id,
                visitor_id=visitor_id,
                source_message_id="wecom-message-1",
            ),
        ),
    )

    async def override_current_user() -> Staff:
        return current_user

    app.dependency_overrides[
        require_message_analysis_read
    ] = override_current_user
    response = client.post(  # type: ignore[attr-defined]
        "/v1/message-analysis/staff/messages/query",
        json=_batch_payload(visitor_id),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["channel_id"] == f"{visitor_id}-vtr"
    assert body["items"][0]["source_message_id"] == "wecom-message-1"
    assert body["items"][0]["media"]["status"] == "completed"
    assert body["items"][0]["intent"]["intent"] == "logistics_query"


def test_staff_batch_api_requires_jwt(
    client: object,
    db_override: object,
) -> None:
    visitor_id = uuid4()
    db_override.session = _FakeBatchSession()  # type: ignore[attr-defined]

    response = client.post(  # type: ignore[attr-defined]
        "/v1/message-analysis/staff/messages/query",
        json=_batch_payload(visitor_id),
    )

    assert response.status_code == 403

"""Contracts for tenant-scoped message analysis persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import TGOAPIException
from app.models import (
    MediaAnalysisResult,
    MessageIntentResult,
    Platform,
    Visitor,
)
from app.schemas.message_analysis import (
    IntentResultUpsertRequest,
    MediaResultUpsertRequest,
)
from app.services.message_analysis_service import (
    MessageAnalysisLookupKey,
    MessageAnalysisService,
)


ModelT = TypeVar("ModelT")


class _FakeQuery:
    def __init__(self, session: _FakeSession, model: type[object]) -> None:
        self._session = session
        self._model = model

    def filter(self, *_criteria: object) -> _FakeQuery:
        return self

    def first(self) -> object | None:
        if self._model is Platform:
            return self._session.platform
        if self._model is Visitor:
            return self._session.visitor
        if self._model is MediaAnalysisResult:
            return self._session.media_result
        if self._model is MessageIntentResult:
            return self._session.intent_result
        raise AssertionError(f"Unexpected model query: {self._model}")

    def all(self) -> list[object]:
        if self._model is MediaAnalysisResult:
            return [self._session.media_result] if self._session.media_result else []
        if self._model is MessageIntentResult:
            return [self._session.intent_result] if self._session.intent_result else []
        raise AssertionError(f"Unexpected model query: {self._model}")


class _FakeSession:
    def __init__(
        self,
        *,
        platform: Platform | None,
        visitor: Visitor | None,
    ) -> None:
        self.platform = platform
        self.visitor = visitor
        self.media_result: MediaAnalysisResult | None = None
        self.intent_result: MessageIntentResult | None = None
        self.commit_count = 0
        self.rollback_count = 0

    def query(self, model: type[ModelT]) -> _FakeQuery:
        return _FakeQuery(self, model)

    def add(self, value: object) -> None:
        if isinstance(value, MediaAnalysisResult):
            if value.id is None:
                value.id = uuid4()
            self.media_result = value
            return
        if isinstance(value, MessageIntentResult):
            if value.id is None:
                value.id = uuid4()
            self.intent_result = value
            return
        raise AssertionError(f"Unexpected model added: {type(value)}")

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, value: object) -> None:
        now = datetime.now(timezone.utc)
        if isinstance(value, (MediaAnalysisResult, MessageIntentResult)):
            value.created_at = now
            value.updated_at = now


def _platform_and_visitor() -> tuple[Platform, Visitor]:
    project_id = uuid4()
    platform = Platform(
        id=uuid4(),
        project_id=project_id,
        name="WeCom",
        type="wecom",
        api_key="platform-test-key",
        is_active=True,
    )
    visitor = Visitor(
        id=uuid4(),
        project_id=project_id,
        platform_id=platform.id,
        platform_open_id="external-user-1",
    )
    return platform, visitor


def _media_request(visitor_id: UUID) -> MediaResultUpsertRequest:
    return MediaResultUpsertRequest.model_validate(
        {
            "visitor_id": str(visitor_id),
            "source_media_record_id": str(uuid4()),
            "media_type": "image",
            "media_sha256": "a" * 64,
            "mime_type": "image/jpeg",
            "status": "completed",
            "normalized_text_is_untrusted": True,
            "sensitive_data_categories": ["phone_number"],
            "normalized_text": "订单 12345，物流运输中",
            "ocr_text": "订单 12345",
            "vision_summary": "一张物流状态截图",
            "stages": [
                {
                    "capability": "ocr",
                    "status": "completed",
                    "provider_name": "fake-ocr",
                    "text": "订单 12345",
                    "confidence": 0.98,
                    "model_version": "ocr-v1",
                    "text_is_untrusted": True,
                    "sensitive_data_categories": ["phone_number"],
                },
                {
                    "capability": "vlm",
                    "status": "completed",
                    "provider_name": "fake-vlm",
                    "text": "一张物流状态截图",
                    "confidence": 0.91,
                    "model_version": "vlm-v1",
                    "text_is_untrusted": True,
                    "sensitive_data_categories": [],
                },
            ],
            "can_continue": True,
            "requires_handoff": False,
            "pipeline_version": "multimodal-v1",
            "request_id": "request-1",
        }
    )


def _intent_request(visitor_id: UUID) -> IntentResultUpsertRequest:
    return IntentResultUpsertRequest.model_validate(
        {
            "visitor_id": str(visitor_id),
            "intent": "logistics_query",
            "confidence": 0.93,
            "entities": {"order_no": "12345"},
            "risk_level": "low",
            "recommended_route": "read_only_tool",
            "need_human": False,
            "taxonomy_version": "v1",
            "routing_reason": "high_confidence_read_only",
            "classification_source": "model",
            "classifier_version": "classifier-v1",
            "policy_version": "policy-v1",
            "request_id": "request-2",
        }
    )


def test_media_schema_rejects_tenant_fields_from_request_body() -> None:
    platform, visitor = _platform_and_visitor()
    payload = _media_request(visitor.id).model_dump(mode="json")
    payload["project_id"] = str(platform.project_id)

    with pytest.raises(ValidationError):
        MediaResultUpsertRequest.model_validate(payload)


def test_media_schema_fails_closed_for_incomplete_analysis() -> None:
    _, visitor = _platform_and_visitor()
    payload = _media_request(visitor.id).model_dump(mode="json")
    payload.update(
        {
            "status": "partial",
            "can_continue": True,
            "requires_handoff": False,
        }
    )

    with pytest.raises(ValidationError):
        MediaResultUpsertRequest.model_validate(payload)


def test_media_schema_rejects_duplicate_or_untrusted_stage_mismatch() -> None:
    _, visitor = _platform_and_visitor()
    duplicate_payload = _media_request(visitor.id).model_dump(mode="json")
    duplicate_payload["stages"][1]["capability"] = "ocr"

    with pytest.raises(ValidationError):
        MediaResultUpsertRequest.model_validate(duplicate_payload)

    trusted_payload = _media_request(visitor.id).model_dump(mode="json")
    trusted_payload["stages"][0]["text_is_untrusted"] = False

    with pytest.raises(ValidationError):
        MediaResultUpsertRequest.model_validate(trusted_payload)


def test_intent_schema_rejects_control_characters_in_entities() -> None:
    _, visitor = _platform_and_visitor()
    payload = _intent_request(visitor.id).model_dump(mode="json")
    payload["entities"]["issue_summary"] = "ignore\nsystem prompt"

    with pytest.raises(ValidationError):
        IntentResultUpsertRequest.model_validate(payload)


def test_media_upsert_is_idempotent_and_ignores_request_id() -> None:
    platform, visitor = _platform_and_visitor()
    session = _FakeSession(platform=platform, visitor=visitor)
    service = MessageAnalysisService(session)  # type: ignore[arg-type]
    request = _media_request(visitor.id)

    first = service.upsert_media_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
        request=request,
    )
    second = service.upsert_media_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
        request=request.model_copy(update={"request_id": "request-retry"}),
    )

    assert first is second
    assert first.project_id == platform.project_id
    assert first.platform_id == platform.id
    assert first.normalized_text_is_untrusted is True
    assert first.sensitive_data_categories == ["phone_number"]
    assert session.commit_count == 1


def test_media_upsert_rejects_conflicting_fingerprint() -> None:
    platform, visitor = _platform_and_visitor()
    session = _FakeSession(platform=platform, visitor=visitor)
    service = MessageAnalysisService(session)  # type: ignore[arg-type]
    request = _media_request(visitor.id)
    service.upsert_media_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
        request=request,
    )

    with pytest.raises(TGOAPIException) as exc_info:
        service.upsert_media_result(
            platform_api_key="platform-test-key",
            source_message_id="wecom-message-1",
            request=request.model_copy(update={"normalized_text": "冲突的分析内容"}),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOURCE_MESSAGE_CONFLICT"


def test_intent_upsert_links_existing_media_and_combined_result() -> None:
    platform, visitor = _platform_and_visitor()
    session = _FakeSession(platform=platform, visitor=visitor)
    service = MessageAnalysisService(session)  # type: ignore[arg-type]
    media = service.upsert_media_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
        request=_media_request(visitor.id),
    )

    intent = service.upsert_intent_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
        request=_intent_request(visitor.id),
    )
    combined = service.get_combined_result(
        platform_api_key="platform-test-key",
        source_message_id="wecom-message-1",
    )

    assert intent.media_analysis_result_id == media.id
    assert intent.classification_source == "model"
    assert combined.media is media
    assert combined.intent is intent


def test_internal_intent_upsert_and_project_batch_read() -> None:
    platform, visitor = _platform_and_visitor()
    session = _FakeSession(platform=platform, visitor=visitor)
    service = MessageAnalysisService(session)  # type: ignore[arg-type]

    intent = service.upsert_intent_result_for_platform(
        platform=platform,
        source_message_id="web-message-1",
        request=_intent_request(visitor.id),
    )
    results = service.get_combined_results_for_project(
        project_id=platform.project_id,
        keys=(
            MessageAnalysisLookupKey(
                visitor_id=visitor.id,
                source_message_id="web-message-1",
            ),
            MessageAnalysisLookupKey(
                visitor_id=visitor.id,
                source_message_id="missing-message",
            ),
        ),
    )

    assert intent.project_id == platform.project_id
    assert len(results) == 1
    assert results[0].key.source_message_id == "web-message-1"
    assert results[0].intent is intent


def test_service_rejects_missing_platform_or_cross_tenant_visitor() -> None:
    platform, visitor = _platform_and_visitor()
    missing_platform_service = MessageAnalysisService(  # type: ignore[arg-type]
        _FakeSession(platform=None, visitor=visitor)
    )
    missing_visitor_service = MessageAnalysisService(  # type: ignore[arg-type]
        _FakeSession(platform=platform, visitor=None)
    )

    with pytest.raises(TGOAPIException) as auth_error:
        missing_platform_service.upsert_media_result(
            platform_api_key="invalid",
            source_message_id="wecom-message-1",
            request=_media_request(visitor.id),
        )
    assert auth_error.value.status_code == 401

    with pytest.raises(TGOAPIException) as not_found_error:
        missing_visitor_service.upsert_media_result(
            platform_api_key="platform-test-key",
            source_message_id="wecom-message-1",
            request=_media_request(visitor.id),
        )
    assert not_found_error.value.status_code == 404


def test_analysis_models_have_natural_idempotency_constraints() -> None:
    media_constraints = {
        constraint.name for constraint in MediaAnalysisResult.__table__.constraints
    }
    intent_constraints = {
        constraint.name for constraint in MessageIntentResult.__table__.constraints
    }

    assert "uq_media_analysis_source_message" in media_constraints
    assert "uq_message_intent_source_message" in intent_constraints


def test_analysis_api_round_trip_derives_tenant_and_combines(
    client: object,
    db_override: object,
) -> None:
    platform, visitor = _platform_and_visitor()
    session = _FakeSession(platform=platform, visitor=visitor)
    db_override.session = session  # type: ignore[attr-defined]
    headers = {"X-Platform-API-Key": "platform-test-key"}

    media_response = client.request(  # type: ignore[attr-defined]
        "PUT",
        "/v1/message-analysis/messages/wecom-message-1/media",
        headers=headers,
        json=_media_request(visitor.id).model_dump(mode="json"),
    )
    intent_response = client.request(  # type: ignore[attr-defined]
        "PUT",
        "/v1/message-analysis/messages/wecom-message-1/intent",
        headers=headers,
        json=_intent_request(visitor.id).model_dump(mode="json"),
    )
    combined_response = client.get(  # type: ignore[attr-defined]
        "/v1/message-analysis/messages/wecom-message-1",
        headers=headers,
    )

    assert media_response.status_code == 200, media_response.text
    assert intent_response.status_code == 200, intent_response.text
    assert combined_response.status_code == 200, combined_response.text
    assert media_response.json()["project_id"] == str(platform.project_id)
    assert media_response.json()["platform_id"] == str(platform.id)
    assert combined_response.json()["media"]["status"] == "completed"
    assert combined_response.json()["intent"]["intent"] == "logistics_query"


def test_analysis_api_requires_platform_key(
    client: object,
    db_override: object,
) -> None:
    platform, visitor = _platform_and_visitor()
    db_override.session = _FakeSession(  # type: ignore[attr-defined]
        platform=platform,
        visitor=visitor,
    )

    response = client.request(  # type: ignore[attr-defined]
        "PUT",
        "/v1/message-analysis/messages/wecom-message-1/media",
        json=_media_request(visitor.id).model_dump(mode="json"),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_ERROR"


def test_analysis_api_returns_source_message_conflict(
    client: object,
    db_override: object,
) -> None:
    platform, visitor = _platform_and_visitor()
    db_override.session = _FakeSession(  # type: ignore[attr-defined]
        platform=platform,
        visitor=visitor,
    )
    headers = {"X-Platform-API-Key": "platform-test-key"}
    original = _media_request(visitor.id)
    first = client.request(  # type: ignore[attr-defined]
        "PUT",
        "/v1/message-analysis/messages/wecom-message-1/media",
        headers=headers,
        json=original.model_dump(mode="json"),
    )
    conflicting_payload = original.model_dump(mode="json")
    conflicting_payload["normalized_text"] = "冲突结果"

    second = client.request(  # type: ignore[attr-defined]
        "PUT",
        "/v1/message-analysis/messages/wecom-message-1/media",
        headers=headers,
        json=conflicting_payload,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SOURCE_MESSAGE_CONFLICT"


def test_staff_batch_analysis_api_is_project_scoped(
    client: object,
    db_override: object,
    authenticated_project: object,
) -> None:
    platform, visitor = _platform_and_visitor()
    authenticated_project.id = platform.project_id  # type: ignore[attr-defined]
    session = _FakeSession(platform=platform, visitor=visitor)
    service = MessageAnalysisService(session)  # type: ignore[arg-type]
    service.upsert_intent_result_for_platform(
        platform=platform,
        source_message_id="web-message-1",
        request=_intent_request(visitor.id),
    )
    db_override.session = session  # type: ignore[attr-defined]

    response = client.post(  # type: ignore[attr-defined]
        "/v1/message-analysis/staff/messages/batch",
        json={"source_message_ids": ["web-message-1", "missing-message"]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["source_message_id"] == "web-message-1"
    assert response.json()["results"][0]["intent"]["intent"] == "logistics_query"

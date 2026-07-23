"""Tenant-scoped persistence for media analysis and intent results."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from collections.abc import Sequence
from uuid import UUID

from fastapi import status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AuthenticationError,
    NotFoundError,
    TGOAPIException,
)
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


class SourceMessageConflictError(TGOAPIException):
    """The same natural message key was retried with different content."""

    def __init__(self, source_message_id: str) -> None:
        super().__init__(
            message="Source message already has a different analysis result",
            code="SOURCE_MESSAGE_CONFLICT",
            details={"source_message_id": source_message_id},
            status_code=status.HTTP_409_CONFLICT,
        )


@dataclass(frozen=True)
class CombinedMessageAnalysis:
    """Current media and intent results for one source message."""

    media: MediaAnalysisResult | None
    intent: MessageIntentResult | None


def _fingerprint(source_message_id: str, request: BaseModel) -> str:
    """Build a deterministic digest while excluding retry-only request IDs."""
    canonical = {
        "source_message_id": source_message_id,
        "result": request.model_dump(
            mode="json",
            exclude={"request_id"},
            exclude_none=False,
        ),
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class MessageAnalysisService:
    """Validate ownership and persist current analysis results idempotently."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def upsert_media_result(
        self,
        *,
        platform_api_key: str,
        source_message_id: str,
        request: MediaResultUpsertRequest,
    ) -> MediaAnalysisResult:
        """Insert a media result or return the identical existing result."""
        platform = self._authenticate_platform(platform_api_key)
        return self.upsert_media_result_for_platform(
            platform=platform,
            source_message_id=source_message_id,
            request=request,
        )

    def upsert_media_result_for_platform(
        self,
        *,
        platform: Platform,
        source_message_id: str,
        request: MediaResultUpsertRequest,
    ) -> MediaAnalysisResult:
        """Persist a result after an in-process caller already resolved the platform."""
        self._require_active_platform(platform)
        self._require_scoped_visitor(platform, request.visitor_id)
        fingerprint = _fingerprint(source_message_id, request)
        existing = self._find_media_result(platform, source_message_id)
        if existing is not None:
            self._require_same_fingerprint(
                existing.input_fingerprint,
                fingerprint,
                source_message_id,
            )
            return existing

        result = MediaAnalysisResult(
            project_id=platform.project_id,
            platform_id=platform.id,
            visitor_id=request.visitor_id,
            source_message_id=source_message_id,
            source_media_record_id=request.source_media_record_id,
            media_type=request.media_type.value,
            media_sha256=request.media_sha256,
            mime_type=request.mime_type,
            status=request.status.value,
            normalized_text=request.normalized_text,
            normalized_text_is_untrusted=(request.normalized_text_is_untrusted),
            sensitive_data_categories=[
                category.value for category in request.sensitive_data_categories
            ],
            transcript=request.transcript,
            ocr_text=request.ocr_text,
            vision_summary=request.vision_summary,
            stages=[stage.model_dump(mode="json") for stage in request.stages],
            can_continue=request.can_continue,
            requires_handoff=request.requires_handoff,
            fallback_message=request.fallback_message,
            pipeline_version=request.pipeline_version,
            input_fingerprint=fingerprint,
            request_id=request.request_id,
        )
        return self._commit_media_result(result, platform, source_message_id)

    def upsert_intent_result(
        self,
        *,
        platform_api_key: str,
        source_message_id: str,
        request: IntentResultUpsertRequest,
    ) -> MessageIntentResult:
        """Insert an intent result or return the identical existing result."""
        platform = self._authenticate_platform(platform_api_key)
        return self.upsert_intent_result_for_platform(
            platform=platform,
            source_message_id=source_message_id,
            request=request,
        )

    def upsert_intent_result_for_platform(
        self,
        *,
        platform: Platform,
        source_message_id: str,
        request: IntentResultUpsertRequest,
    ) -> MessageIntentResult:
        """Persist an intent result for a trusted in-process platform context."""
        self._require_active_platform(platform)
        self._require_scoped_visitor(platform, request.visitor_id)
        media_result = self._resolve_media_result(
            platform,
            source_message_id,
            request,
        )
        media_result_id = media_result.id if media_result is not None else None
        normalized_request = request.model_copy(
            update={"media_analysis_result_id": media_result_id}
        )
        fingerprint = _fingerprint(source_message_id, normalized_request)
        existing = self._find_intent_result(platform, source_message_id)
        if existing is not None:
            self._require_same_fingerprint(
                existing.input_fingerprint,
                fingerprint,
                source_message_id,
            )
            return existing

        result = MessageIntentResult(
            project_id=platform.project_id,
            platform_id=platform.id,
            visitor_id=request.visitor_id,
            source_message_id=source_message_id,
            media_analysis_result_id=media_result_id,
            intent=request.intent.value,
            confidence=request.confidence,
            entities=request.entities.model_dump(mode="json"),
            risk_level=request.risk_level.value,
            recommended_route=request.recommended_route.value,
            need_human=request.need_human,
            taxonomy_version=request.taxonomy_version,
            routing_reason=request.routing_reason.value,
            classification_source=request.classification_source.value,
            classifier_version=request.classifier_version,
            policy_version=request.policy_version,
            input_fingerprint=fingerprint,
            request_id=request.request_id,
        )
        return self._commit_intent_result(result, platform, source_message_id)

    def get_combined_result(
        self,
        *,
        platform_api_key: str,
        source_message_id: str,
    ) -> CombinedMessageAnalysis:
        """Return current media and intent results within the key's tenant."""
        platform = self._authenticate_platform(platform_api_key)
        media = self._find_media_result(platform, source_message_id)
        intent = self._find_intent_result(platform, source_message_id)
        if media is None and intent is None:
            raise NotFoundError("Message analysis", source_message_id)
        return CombinedMessageAnalysis(media=media, intent=intent)

    def get_combined_results_for_project(
        self,
        *,
        project_id: UUID,
        source_message_ids: Sequence[str],
    ) -> dict[str, CombinedMessageAnalysis]:
        """Batch-read only records owned by one authenticated project."""
        if not source_message_ids:
            return {}
        media_results = (
            self._db.query(MediaAnalysisResult)
            .filter(
                MediaAnalysisResult.project_id == project_id,
                MediaAnalysisResult.source_message_id.in_(source_message_ids),
            )
            .all()
        )
        intent_results = (
            self._db.query(MessageIntentResult)
            .filter(
                MessageIntentResult.project_id == project_id,
                MessageIntentResult.source_message_id.in_(source_message_ids),
            )
            .all()
        )
        combined: dict[str, CombinedMessageAnalysis] = {}
        for media in media_results:
            combined[media.source_message_id] = CombinedMessageAnalysis(
                media=media,
                intent=None,
            )
        for intent in intent_results:
            current = combined.get(intent.source_message_id)
            combined[intent.source_message_id] = CombinedMessageAnalysis(
                media=current.media if current is not None else None,
                intent=intent,
            )
        return combined

    def _authenticate_platform(self, api_key: str) -> Platform:
        if not api_key:
            raise AuthenticationError("Missing platform API key")
        platform = (
            self._db.query(Platform)
            .filter(
                Platform.api_key == api_key,
                Platform.is_active.is_(True),
                Platform.deleted_at.is_(None),
            )
            .first()
        )
        if platform is None:
            raise AuthenticationError("Invalid platform API key")
        return platform

    @staticmethod
    def _require_active_platform(platform: Platform) -> None:
        if not platform.is_active or platform.deleted_at is not None:
            raise AuthenticationError("Inactive platform")

    def _require_scoped_visitor(
        self,
        platform: Platform,
        visitor_id: UUID,
    ) -> Visitor:
        visitor = (
            self._db.query(Visitor)
            .filter(
                Visitor.id == visitor_id,
                Visitor.project_id == platform.project_id,
                Visitor.platform_id == platform.id,
                Visitor.deleted_at.is_(None),
            )
            .first()
        )
        if visitor is None:
            raise NotFoundError("Visitor")
        return visitor

    def _resolve_media_result(
        self,
        platform: Platform,
        source_message_id: str,
        request: IntentResultUpsertRequest,
    ) -> MediaAnalysisResult | None:
        media = self._find_media_result(platform, source_message_id)
        if request.media_analysis_result_id is None:
            return media
        if media is None or media.id != request.media_analysis_result_id:
            raise NotFoundError(
                "Media analysis result",
                str(request.media_analysis_result_id),
            )
        if media.visitor_id != request.visitor_id:
            raise NotFoundError("Media analysis result")
        return media

    def _find_media_result(
        self,
        platform: Platform,
        source_message_id: str,
    ) -> MediaAnalysisResult | None:
        return (
            self._db.query(MediaAnalysisResult)
            .filter(
                MediaAnalysisResult.project_id == platform.project_id,
                MediaAnalysisResult.platform_id == platform.id,
                MediaAnalysisResult.source_message_id == source_message_id,
            )
            .first()
        )

    def _find_intent_result(
        self,
        platform: Platform,
        source_message_id: str,
    ) -> MessageIntentResult | None:
        return (
            self._db.query(MessageIntentResult)
            .filter(
                MessageIntentResult.project_id == platform.project_id,
                MessageIntentResult.platform_id == platform.id,
                MessageIntentResult.source_message_id == source_message_id,
            )
            .first()
        )

    def _commit_media_result(
        self,
        result: MediaAnalysisResult,
        platform: Platform,
        source_message_id: str,
    ) -> MediaAnalysisResult:
        self._db.add(result)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = self._find_media_result(platform, source_message_id)
            if existing is None:
                raise SourceMessageConflictError(source_message_id)
            self._require_same_fingerprint(
                existing.input_fingerprint,
                result.input_fingerprint,
                source_message_id,
            )
            return existing
        self._db.refresh(result)
        return result

    def _commit_intent_result(
        self,
        result: MessageIntentResult,
        platform: Platform,
        source_message_id: str,
    ) -> MessageIntentResult:
        self._db.add(result)
        try:
            self._db.commit()
        except IntegrityError:
            self._db.rollback()
            existing = self._find_intent_result(platform, source_message_id)
            if existing is None:
                raise SourceMessageConflictError(source_message_id)
            self._require_same_fingerprint(
                existing.input_fingerprint,
                result.input_fingerprint,
                source_message_id,
            )
            return existing
        self._db.refresh(result)
        return result

    @staticmethod
    def _require_same_fingerprint(
        existing: str,
        incoming: str,
        source_message_id: str,
    ) -> None:
        if existing != incoming:
            raise SourceMessageConflictError(source_message_id)

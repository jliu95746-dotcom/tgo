"""API contracts for knowledge governance management."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.rag_service.models.knowledge_governance import KnowledgeGovernanceRecord
from src.rag_service.routers.knowledge_governance import router
from src.rag_service.schemas.knowledge_governance import (
    KnowledgeChannel,
    KnowledgeDocumentType,
    KnowledgeGovernanceBackfillRequest,
    KnowledgeGovernanceDraftRequest,
    KnowledgeReviewStatus,
    KnowledgeSourceOrigin,
)
from src.rag_service.services.knowledge_governance import KnowledgeGovernanceService


def _draft_request() -> KnowledgeGovernanceDraftRequest:
    return KnowledgeGovernanceDraftRequest(
        document_type=KnowledgeDocumentType.PRODUCT,
        product_line="智能客服",
        channels=(KnowledgeChannel.WECOM_KF, KnowledgeChannel.WEB),
        effective_at=datetime(2026, 7, 17, tzinfo=UTC),
        owner="产品运营",
        document_version="v1.0",
        allow_automatic_reply=True,
        source_origin=KnowledgeSourceOrigin.INTERNAL,
    )


def test_router_exposes_governance_management_endpoints() -> None:
    route_signatures = {(route.path, next(iter(route.methods))) for route in router.routes}

    assert ("", "GET") in route_signatures
    assert ("/files/{file_id}", "PUT") in route_signatures
    assert ("/{record_id}/submit", "POST") in route_signatures
    assert ("/{record_id}/review", "POST") in route_signatures
    assert ("/backfill", "POST") in route_signatures


def test_draft_update_resets_rejected_audit_state() -> None:
    record = KnowledgeGovernanceRecord(
        project_id=uuid4(),
        file_id=uuid4(),
        document_type=KnowledgeDocumentType.FAQ.value,
        product_line="旧产品线",
        channels=[KnowledgeChannel.INTERNAL.value],
        effective_at=datetime(2026, 7, 1, tzinfo=UTC),
        expires_at=None,
        owner="旧负责人",
        document_version="v0.9",
        allow_automatic_reply=False,
        review_status=KnowledgeReviewStatus.REJECTED.value,
        reviewed_by="reviewer",
        reviewed_at=datetime(2026, 7, 2, tzinfo=UTC),
        source_origin=KnowledgeSourceOrigin.INTERNAL.value,
    )

    KnowledgeGovernanceService.update_draft(record, _draft_request())

    assert record.review_status == KnowledgeReviewStatus.DRAFT.value
    assert record.reviewed_by is None
    assert record.reviewed_at is None
    assert record.product_line == "智能客服"
    assert record.allow_automatic_reply is True


def test_backfill_request_is_fail_closed() -> None:
    request = KnowledgeGovernanceBackfillRequest(
        collection_id=uuid4(),
        document_type=KnowledgeDocumentType.SOP,
        product_line="售后",
        channels=(KnowledgeChannel.WECOM_KF,),
        effective_at=datetime(2026, 7, 17, tzinfo=UTC),
        owner="知识管理员",
        document_version="legacy-v1",
        source_origin=KnowledgeSourceOrigin.INTERNAL,
        dry_run=False,
    )

    assert request.allow_automatic_reply is False
    assert request.review_status is KnowledgeReviewStatus.DRAFT

    with pytest.raises(ValidationError):
        KnowledgeGovernanceBackfillRequest(
            collection_id=uuid4(),
            document_type=KnowledgeDocumentType.SOP,
            product_line="售后",
            channels=(KnowledgeChannel.WECOM_KF,),
            effective_at=datetime(2026, 7, 17, tzinfo=UTC),
            owner="知识管理员",
            document_version="legacy-v1",
            allow_automatic_reply=True,
        )

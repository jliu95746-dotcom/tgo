"""Availability and channel edits must preserve the existing admission rules."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.rag_service.schemas.knowledge_availability import ChannelUpdateRequest
from src.rag_service.services.knowledge_availability import (
    apply_channel_update,
    collection_counts_statement,
)
from src.rag_service.services.knowledge_governance import (
    InvalidReviewTransitionError,
    KnowledgeGovernanceService,
)
from tests.test_knowledge_governance import governance_input, NOW


def test_availability_uses_the_same_tenant_channel_review_and_expiry_gates():
    project = uuid4()
    statement = collection_counts_statement(project, "wecom_kf", NOW)
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={
                "literal_binds": True,
            },
        )
    )
    for required in (
        str(project),
        "wecom_kf",
        "approved",
        "expires_at",
        "allow_automatic_reply",
        "rag_files",
        "rag_qa_pairs",
    ):
        assert required in sql
    assert "rag_file_documents.project_id" in sql


def test_channel_update_changes_only_channels_and_review_audit():
    record = KnowledgeGovernanceService.new_record(uuid4(), governance_input())
    record.updated_at = NOW
    before = {c.name: getattr(record, c.name) for c in record.__table__.columns}
    request = ChannelUpdateRequest(
        channels=["web", "wecom_kf"], expected_updated_at=NOW
    )
    apply_channel_update(
        record, request, reviewer="authorized-admin", at=NOW + timedelta(seconds=1)
    )
    assert record.channels == ["web", "wecom_kf"]
    assert record.reviewed_by == "authorized-admin"
    assert record.reviewed_at == NOW + timedelta(seconds=1)
    for key, value in before.items():
        if key not in {"channels", "reviewed_by", "reviewed_at", "updated_at"}:
            assert getattr(record, key) == value


@pytest.mark.parametrize("state", ["draft", "rejected", "pending_review", "revoked"])
def test_channel_edit_cannot_approve_unreviewed_content(state):
    record = KnowledgeGovernanceService.new_record(uuid4(), governance_input())
    record.review_status, record.updated_at = state, NOW
    with pytest.raises(InvalidReviewTransitionError):
        apply_channel_update(
            record,
            ChannelUpdateRequest(channels=["wecom_kf"], expected_updated_at=NOW),
            reviewer="admin",
            at=NOW,
        )
    assert record.review_status == state


def test_stale_channel_editor_cannot_overwrite_a_new_review():
    record = KnowledgeGovernanceService.new_record(uuid4(), governance_input())
    record.updated_at = NOW + timedelta(seconds=1)
    with pytest.raises(InvalidReviewTransitionError, match="已变化"):
        apply_channel_update(
            record,
            ChannelUpdateRequest(channels=["wecom_kf"], expected_updated_at=NOW),
            reviewer="admin",
            at=NOW,
        )

"""Contract tests for fail-closed customer-service knowledge governance."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from src.rag_service.models.knowledge_governance import KnowledgeGovernanceRecord
from src.rag_service.schemas.knowledge_governance import (
    AutomaticAnswerEligibilityReason,
    KnowledgeChannel,
    KnowledgeDocumentType,
    KnowledgeGovernanceInput,
    KnowledgeReviewDecision,
    KnowledgeReviewStatus,
    KnowledgeSourceOrigin,
)
from src.rag_service.services.knowledge_governance import (
    InvalidReviewTransitionError,
    KnowledgeGovernancePolicy,
    KnowledgeGovernanceService,
)


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def governance_input(**overrides: object) -> KnowledgeGovernanceInput:
    """Build a valid approved governance contract."""
    values: dict[str, object] = {
        "file_id": uuid4(),
        "qa_pair_id": None,
        "document_type": KnowledgeDocumentType.PRODUCT,
        "product_line": "智能客服",
        "channels": (KnowledgeChannel.WECOM_KF,),
        "effective_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=30),
        "owner": "客服产品组",
        "document_version": "2026.07.1",
        "allow_automatic_reply": True,
        "review_status": KnowledgeReviewStatus.APPROVED,
        "reviewed_by": "reviewer@example.invalid",
        "reviewed_at": NOW - timedelta(hours=1),
        "source_origin": KnowledgeSourceOrigin.INTERNAL,
    }
    values.update(overrides)
    return KnowledgeGovernanceInput.model_validate(values)


def test_contract_requires_exactly_one_governed_source() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        governance_input(file_id=None)

    with pytest.raises(ValidationError, match="exactly one"):
        governance_input(qa_pair_id=uuid4())


def test_contract_rejects_invalid_validity_and_review_audit() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        governance_input(effective_at=datetime(2026, 7, 15, 12, 0))

    with pytest.raises(ValidationError, match="later than effective_at"):
        governance_input(expires_at=NOW - timedelta(days=2))

    with pytest.raises(ValidationError, match="reviewed_by and reviewed_at"):
        governance_input(reviewed_by=None)


def test_customer_and_website_sources_are_marked_untrusted() -> None:
    customer = governance_input(source_origin=KnowledgeSourceOrigin.CUSTOMER)
    website = governance_input(source_origin=KnowledgeSourceOrigin.WEBSITE)
    internal = governance_input(source_origin=KnowledgeSourceOrigin.INTERNAL)

    assert customer.content_is_untrusted is True
    assert website.content_is_untrusted is True
    assert internal.content_is_untrusted is False


def test_approved_current_internal_knowledge_is_eligible() -> None:
    decision = KnowledgeGovernancePolicy.evaluate(
        governance_input(),
        at=NOW,
        channel=KnowledgeChannel.WECOM_KF,
    )

    assert decision.eligible is True
    assert decision.reason is AutomaticAnswerEligibilityReason.ELIGIBLE
    assert decision.content_is_untrusted is False


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"review_status": KnowledgeReviewStatus.PENDING_REVIEW,
             "reviewed_by": None, "reviewed_at": None},
            AutomaticAnswerEligibilityReason.NOT_APPROVED,
        ),
        (
            {"effective_at": NOW + timedelta(seconds=1),
             "expires_at": NOW + timedelta(days=2)},
            AutomaticAnswerEligibilityReason.NOT_YET_EFFECTIVE,
        ),
        (
            {"expires_at": NOW},
            AutomaticAnswerEligibilityReason.EXPIRED,
        ),
        (
            {"allow_automatic_reply": False},
            AutomaticAnswerEligibilityReason.AUTOMATIC_REPLY_DISABLED,
        ),
        (
            {"source_origin": KnowledgeSourceOrigin.CUSTOMER},
            AutomaticAnswerEligibilityReason.CUSTOMER_CONTENT,
        ),
    ],
)
def test_ineligible_knowledge_fails_closed(
    overrides: dict[str, object],
    reason: AutomaticAnswerEligibilityReason,
) -> None:
    decision = KnowledgeGovernancePolicy.evaluate(
        governance_input(**overrides),
        at=NOW,
        channel=KnowledgeChannel.WECOM_KF,
    )

    assert decision.eligible is False
    assert decision.reason is reason


def test_reviewed_website_knowledge_remains_untrusted_but_can_be_retrieved() -> None:
    decision = KnowledgeGovernancePolicy.evaluate(
        governance_input(source_origin=KnowledgeSourceOrigin.WEBSITE),
        at=NOW,
        channel=KnowledgeChannel.WECOM_KF,
    )

    assert decision.eligible is True
    assert decision.content_is_untrusted is True


def test_review_transition_requires_pending_review() -> None:
    record = KnowledgeGovernanceService.new_record(
        project_id=uuid4(),
        data=governance_input(
            review_status=KnowledgeReviewStatus.DRAFT,
            reviewed_by=None,
            reviewed_at=None,
        ),
    )
    decision = KnowledgeReviewDecision(
        status=KnowledgeReviewStatus.APPROVED,
        reviewer="reviewer@example.invalid",
        reviewed_at=NOW,
    )

    with pytest.raises(InvalidReviewTransitionError):
        KnowledgeGovernanceService.apply_review_decision(record, decision)

    KnowledgeGovernanceService.submit_for_review(record)
    KnowledgeGovernanceService.apply_review_decision(record, decision)

    assert record.review_status == KnowledgeReviewStatus.APPROVED.value
    assert record.reviewed_by == "reviewer@example.invalid"
    assert record.reviewed_at == NOW


def test_retrieval_statement_enforces_all_governance_gates() -> None:
    project_id = uuid4()
    candidate_id = uuid4()
    statement = KnowledgeGovernancePolicy.eligible_document_ids_statement(
        project_id=project_id,
        at=NOW,
        channel=KnowledgeChannel.WECOM_KF,
        candidate_ids=(candidate_id,),
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "review_status = 'approved'" in sql
    assert "allow_automatic_reply IS true" in sql
    assert "effective_at <=" in sql
    assert "expires_at IS NULL" in sql
    assert "source_origin != 'customer'" in sql
    assert "deleted_at IS NULL" in sql
    assert "rag_files.project_id" in sql
    assert "rag_files.deleted_at IS NULL" in sql
    assert "channels @> ARRAY['wecom_kf']" in sql
    assert str(candidate_id) in sql


def test_model_declares_source_and_validity_guards() -> None:
    constraint_names = {
        constraint.name
        for constraint in KnowledgeGovernanceRecord.__table__.constraints
    }

    assert "ck_rag_knowledge_governance_exactly_one_source" in constraint_names
    assert "ck_rag_knowledge_governance_valid_window" in constraint_names
    assert "ck_rag_knowledge_governance_review_status" in constraint_names
    assert "ck_rag_knowledge_governance_channels" in constraint_names
    assert "ck_rag_knowledge_governance_nonblank_metadata" in constraint_names


def test_contract_rejects_duplicate_channels_and_blank_ownership() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        governance_input(
            channels=(KnowledgeChannel.WECOM_KF, KnowledgeChannel.WECOM_KF),
        )

    with pytest.raises(ValidationError):
        governance_input(product_line="   ")

    with pytest.raises(ValidationError):
        governance_input(owner="\t")


def test_channel_mismatch_is_denied() -> None:
    decision = KnowledgeGovernancePolicy.evaluate(
        governance_input(),
        at=NOW,
        channel=KnowledgeChannel.APP,
    )

    assert decision.eligible is False
    assert decision.reason is AutomaticAnswerEligibilityReason.CHANNEL_NOT_ALLOWED


def test_naive_evaluation_time_is_denied() -> None:
    decision = KnowledgeGovernancePolicy.evaluate(
        governance_input(),
        at=datetime(2026, 7, 16, 12, 0),
        channel=KnowledgeChannel.WECOM_KF,
    )

    assert decision.eligible is False
    assert decision.reason is AutomaticAnswerEligibilityReason.INVALID_TIME_CONTEXT

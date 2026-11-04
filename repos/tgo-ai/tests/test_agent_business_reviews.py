"""Independent, immutable business judgments cannot promote stale test evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewChecks,
    BusinessTestEvidence,
)
from app.schemas.agent_business_trials import BusinessTrialCheck
from app.services.agent_business_reviews import AgentBusinessReviewService
from tests.test_agent_business_trials import trial, receipt


@pytest_asyncio.fixture
async def review_fixture(trial):
    db, trials, owner, fingerprint, project, actor, agent, request = trial
    report, command = await trials.prepare(project, actor, agent, request)
    await trials.authorize(BusinessTrialCheck(command=command, phase="before_query"))
    await trials.finish(command, receipt(command))
    owner.get_agent_business_test_evidence.side_effect = lambda q: BusinessTestEvidence(
        **q.model_dump(), status="current", issues=[], checked_at=datetime.now(UTC)
    )
    service = AgentBusinessReviewService(db, trials)
    approved = BusinessReviewRequest(
        request_id=uuid4(),
        expected_latest_review_id=None,
        decision="passed",
        checks=BusinessReviewChecks(
            ownership_checked=True, result_checked=True, readonly_scope_checked=True
        ),
        reason=None,
    )
    yield (
        db,
        trials,
        owner,
        fingerprint,
        project,
        actor,
        agent,
        report.id,
        service,
        approved,
    )


@pytest.mark.asyncio
async def test_review_is_immutable_and_latest_decision_controls_current_status(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    first = await service.review(project, actor, agent, report, approved)
    assert (
        first.revision == 1 and first.decision == "passed" and first.can_enable is False
    )
    replay = await service.review(project, actor, agent, report, approved)
    assert replay == first
    state = await service.state(project, actor, agent, report)
    assert (
        state.status == "current"
        and state.review_status == "passed"
        and state.latest_review.id == first.id
    )
    rejected = await service.review(
        project,
        actor,
        agent,
        report,
        BusinessReviewRequest(
            request_id=uuid4(),
            expected_latest_review_id=first.id,
            decision="rejected",
            checks=BusinessReviewChecks(),
            reason="wrong_result",
        ),
    )
    assert rejected.revision == 2
    assert (
        await service.request_result(project, actor, agent, report, approved.request_id)
        == first
    )
    state = await service.state(project, actor, agent, report)
    assert (
        state.latest_review.id == rejected.id
        and state.review_status == "rejected"
        and len(state.reviews) == 2
    )


@pytest.mark.asyncio
async def test_review_request_and_expected_revision_must_match(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    first = await service.review(project, actor, agent, report, approved)
    with pytest.raises(ConflictError):
        await service.review(
            project,
            actor,
            agent,
            report,
            approved.model_copy(
                update={"decision": "rejected", "reason": "wrong_result"}
            ),
        )
    with pytest.raises(ConflictError):
        await service.review(
            project,
            actor,
            agent,
            report,
            approved.model_copy(update={"request_id": uuid4()}),
        )
    assert (
        await service.state(project, actor, agent, report)
    ).latest_review.id == first.id


@pytest.mark.asyncio
async def test_approval_requires_all_explicit_checks(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    with pytest.raises(ConflictError):
        await service.review(
            project,
            actor,
            agent,
            report,
            approved.model_copy(update={"checks": BusinessReviewChecks()}),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["fingerprint", "customer", "proof", "mapping", "failed", "expired"]
)
async def test_stale_or_incomplete_business_evidence_cannot_be_approved(
    review_fixture, change
):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    if change == "fingerprint":
        fingerprint.return_value = "c" * 64
    if change == "customer":
        owner.get_agent_business_test_evidence.side_effect = lambda q: (
            BusinessTestEvidence(
                **q.model_dump(),
                status="changed",
                issues=["identity_changed"],
                checked_at=datetime.now(UTC),
            )
        )
    if change == "proof":
        original = owner.get_agent_business_connection.side_effect

        def changed(q):
            value = original(q)
            value.configuration_tests[0].report_id = uuid4()
            return value

        owner.get_agent_business_connection.side_effect = changed
    if change in {"mapping", "failed", "expired"}:
        from app.schemas.agent_business_trials import BusinessTrialReceipt

        row = await db.get(AgentBusinessTrial, report)
        result = BusinessTrialReceipt.model_validate_json(row.receipt_json)
        if change == "mapping":
            result.summary.status = "结果标签需配置映射"
        if change == "failed":
            result = result.model_copy(
                update={"outcome": "failed", "summary": None, "error_code": "not_found"}
            )
            row.error_code = "not_found"
        if change == "expired":
            observed = datetime.now(UTC) - timedelta(days=8)
            row.created_at = observed
            row.completed_at = observed
            result = result.model_copy(update={"checked_at": observed})
        row.receipt_json = result.model_dump_json()
        await db.commit()
    state = await service.state(project, actor, agent, report)
    assert state.review_status != "passed" and not state.can_review
    with pytest.raises(ConflictError):
        await service.review(project, actor, agent, report, approved)


@pytest.mark.asyncio
async def test_current_approval_invalidates_after_config_change_but_receipt_remains_readable(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    record = await service.review(project, actor, agent, report, approved)
    fingerprint.return_value = "c" * 64
    state = await service.state(project, actor, agent, report)
    assert state.status == "changed" and state.review_status == "needs_retest"
    assert (
        await service.request_result(project, actor, agent, report, approved.request_id)
        == record
    )


@pytest.mark.asyncio
async def test_customer_evidence_check_is_metadata_only_and_actor_revocation_is_not_hidden(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, service, approved = (
        review_fixture
    )
    await service.state(project, actor, agent, report)
    query = owner.get_agent_business_test_evidence.await_args.args[0]
    assert (
        not {"customer_id", "customer_session", "order_no", "credentials"}
        & query.model_dump().keys()
    )
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("revoked")
    with pytest.raises(AuthorizationError):
        await service.state(project, actor, agent, report)

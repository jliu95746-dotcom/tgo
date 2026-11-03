"""Configuration, query completion, review and formal execution remain distinct."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.models.agent_business_trial import AgentBusinessTrial
from app.services.agent_business_capabilities import AgentBusinessCapabilityService
from tests.test_agent_business_reviews import review_fixture
from tests.test_agent_business_trials import trial


@pytest.mark.asyncio
async def test_operation_requires_current_review_and_never_grants_formal_execution(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, approved = (
        review_fixture
    )
    service = AgentBusinessCapabilityService(db, reviews)
    before = await service.read(project, actor, agent)
    assert (
        before.capabilities[0].status == "review_required"
        and not before.can_execute
        and not before.can_enable
    )
    record = await reviews.review(project, actor, agent, report, approved)
    after = await service.read(project, actor, agent)
    assert (
        after.capabilities[0].status == "passed"
        and after.capabilities[0].review_id == record.id
    )
    assert (
        after.capabilities[0].operation == "order_query"
        and len(after.capabilities) == 1
    )


@pytest.mark.asyncio
async def test_new_running_test_supersedes_old_approved_operation(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, approved = (
        review_fixture
    )
    await reviews.review(project, actor, agent, report, approved)
    from app.schemas.agent_business_trials import BusinessTrialRequest

    request = BusinessTrialRequest(
        request_id=uuid4(),
        connection_id=(await trials.get(project, actor, agent, report)).connection_id,
        operation="order_query",
        visitor_id=uuid4(),
        order_no="SECOND-PRIVATE-ORDER",
        customer_session="another-private-session",
    )
    latest, _ = await trials.prepare(project, actor, agent, request)
    state = await AgentBusinessCapabilityService(db, reviews).read(
        project, actor, agent
    )
    assert (
        state.capabilities[0].status == "testing"
        and state.capabilities[0].report_id == latest.id
    )
    assert (
        await reviews.state(project, actor, agent, report)
    ).review_status == "needs_retest"


@pytest.mark.asyncio
async def test_execution_lease_expiry_does_not_expire_completed_review_evidence(
    review_fixture, monkeypatch
):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, approved = (
        review_fixture
    )
    await reviews.review(project, actor, agent, report, approved)
    from app.services import (
        agent_business_trials,
        agent_business_reviews,
        agent_business_review_evidence,
        agent_business_capabilities,
    )

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(minutes=5)

    for module in (
        agent_business_trials,
        agent_business_reviews,
        agent_business_review_evidence,
        agent_business_capabilities,
    ):
        monkeypatch.setattr(module, "datetime", Later)
    state = await AgentBusinessCapabilityService(db, reviews).read(
        project, actor, agent
    )
    assert state.capabilities[0].status == "passed"
    assert state.checked_at > (
        await db.get(AgentBusinessTrial, report)
    ).expires_at.replace(tzinfo=UTC)
    assert (await trials.get(project, actor, agent, report)).status == "completed"


@pytest.mark.asyncio
async def test_unknown_current_owner_does_not_reuse_an_older_passed_badge(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, approved = (
        review_fixture
    )
    await reviews.review(project, actor, agent, report, approved)
    owner.get_agent_business_test_evidence.side_effect = RuntimeError(
        "owner unavailable"
    )
    state = await AgentBusinessCapabilityService(db, reviews).read(
        project, actor, agent
    )
    assert state.capabilities[0].status == "unavailable" and not state.can_execute

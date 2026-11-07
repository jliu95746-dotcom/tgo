"""Exact business evidence changes revoke an earlier confirmation/run identity."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_activation_policy import AgentActivationPolicy
from app.schemas.agent_business_reviews import BusinessReviewRequest, BusinessReviewChecks
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_business_evidence_revision import business_evidence_revision
from app.services.agent_execution_reference import reference_for
from app.services.agent_execution_scope import ExecutionRevoked, track_execution_scope
from tests.test_agent_business_reviews import review_fixture as review_fixture
from tests.test_agent_business_trials import trial as trial
from tests.test_agent_knowledge_trial import employee
from tests.test_agent_activation_readiness import setup as setup


@pytest.mark.asyncio
async def test_append_only_review_changes_the_same_configuration_run_identity(review_fixture):
    db, _, _, _, project, actor, agent, report, reviews, approved = review_fixture
    db.add(AgentActivationPolicy(project_id=project, agent_id=agent, channel="web",
        template_id="logistics", template_version=1, revision=1,
        requirements_revision="c" * 64, created_by=actor, updated_by=actor))
    await db.commit()
    internal = employee().model_copy(update={"id": agent, "project_id": str(project)})
    before = await reference_for(db, project, internal, KnowledgeChannel.WEB)
    first = await reviews.review(project, actor, agent, report, approved)
    current = await reference_for(db, project, internal, KnowledgeChannel.WEB)
    assert before.configuration_fingerprint == current.configuration_fingerprint
    assert before.policy_revision == current.policy_revision
    assert before.business_evidence_revision != current.business_evidence_revision
    assert current.business_evidence_revision == await business_evidence_revision(db, project, actor, agent)
    await reviews.review(project, actor, agent, report, BusinessReviewRequest(
        request_id=uuid4(), expected_latest_review_id=first.id, decision="passed",
        checks=BusinessReviewChecks(ownership_checked=True, result_checked=True, readonly_scope_checked=True), reason=None))
    latest = await reference_for(db, project, internal, KnowledgeChannel.WEB)
    assert latest.business_evidence_revision != current.business_evidence_revision
    async with track_execution_scope(current, AsyncMock(return_value=latest)) as scope:
        with pytest.raises(ExecutionRevoked):
            await scope.check()


@pytest.mark.asyncio
async def test_revision_is_scope_bound_stable_and_includes_connection_revisions(trial):
    db, _, _, _, project, actor, agent, _ = trial
    original = await business_evidence_revision(db, project, actor, agent)
    assert original and len(original) == 64
    assert original == await business_evidence_revision(db, project, actor, agent)
    assert original != await business_evidence_revision(db, project, uuid4(), agent)
    assert await business_evidence_revision(db, uuid4(), actor, agent) is None
    assert await business_evidence_revision(db, project, actor, uuid4()) is None
    binding = await db.scalar(select(AgentBusinessConnection).where(AgentBusinessConnection.agent_id == agent))
    binding.authorization_revision += 1
    await db.commit()
    changed = await business_evidence_revision(db, project, actor, agent)
    assert changed != original
    binding.operations = ["logistics_query", "order_query"]
    await db.commit()
    both = await business_evidence_revision(db, project, actor, agent)
    assert both != changed
    binding.operations = ["order_query", "logistics_query"]
    await db.commit()
    assert await business_evidence_revision(db, project, actor, agent) == both
    binding.deleted_at = datetime.now(UTC)
    await db.commit()
    assert await business_evidence_revision(db, project, actor, agent) is None


@pytest.mark.asyncio
async def test_knowledge_only_run_keeps_no_business_evidence(setup):
    db, stored, _, _, internal, _, _, _, _ = setup
    reference = await reference_for(db, stored.project_id, internal, KnowledgeChannel.WEB)
    assert reference.business_evidence_revision is None

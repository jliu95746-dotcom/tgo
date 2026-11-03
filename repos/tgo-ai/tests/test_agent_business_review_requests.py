"""Original review commands survive disconnects without changing a decision."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.exceptions import AuthorizationError, ConflictError
from app.models.agent_business_review import AgentBusinessReview
from app.models.agent_business_review_request import AgentBusinessReviewRequest
from app.services.agent_business_review_requests import AgentBusinessReviewRequests
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.services.agent_business_trials import AgentBusinessTrialService
from tests.test_agent_business_reviews import review_fixture
from tests.test_agent_business_trials import trial


@pytest.mark.asyncio
async def test_pending_review_restores_the_exact_original_command(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    await requests.register(project, actor, agent, report, command)
    state = await requests.state(project, actor, agent, report, command.request_id)
    assert state.status == "pending" and state.can_resume and state.command == command
    assert state.can_enable is False and state.result is None
    restored = await requests.resume(project, actor, agent, report, command.request_id)
    assert restored.decision == command.decision and restored.checks == command.checks
    assert (
        await requests.state(project, actor, agent, report, command.request_id)
    ).result == restored
    assert len((await db.scalars(select(AgentBusinessReview))).all()) == 1


@pytest.mark.asyncio
async def test_cancel_before_review_registration_fences_late_submission(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    cancelled = await requests.cancel(project, actor, agent, report, command.request_id)
    assert (
        cancelled.status == "cancelled"
        and not cancelled.can_resume
        and cancelled.command is None
    )
    with pytest.raises(ConflictError):
        await reviews.review(project, actor, agent, report, command)
    assert await db.scalar(select(AgentBusinessReview.id)) is None


@pytest.mark.asyncio
async def test_cancellation_during_external_check_blocks_late_approval(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    entered, release = asyncio.Event(), asyncio.Event()
    previous = owner.get_agent_business_test_evidence.side_effect

    async def delayed(query):
        entered.set()
        await release.wait()
        return previous(query)

    owner.get_agent_business_test_evidence.side_effect = delayed
    task = asyncio.create_task(reviews.review(project, actor, agent, report, command))
    await asyncio.wait_for(entered.wait(), 3)
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        independent = AgentBusinessReviewService(
            other,
            AgentBusinessTrialService(other, owner=owner, fingerprint=fingerprint),
        )
        cancelled = await AgentBusinessReviewRequests(independent).cancel(
            project, actor, agent, report, command.request_id
        )
    assert cancelled.status == "cancelled"
    release.set()
    with pytest.raises(ConflictError):
        await task
    assert await db.scalar(select(AgentBusinessReview.id)) is None


@pytest.mark.asyncio
async def test_original_command_cannot_be_changed_during_retry(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    await requests.register(project, actor, agent, report, command)
    with pytest.raises(ConflictError):
        await reviews.review(
            project,
            actor,
            agent,
            report,
            command.model_copy(update={"expected_latest_review_id": uuid4()}),
        )
    state = await requests.state(project, actor, agent, report, command.request_id)
    assert state.command == command and state.status == "pending"


@pytest.mark.asyncio
async def test_timeout_is_terminal_and_does_not_install_an_approval(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )

    async def delayed(_query):
        await asyncio.Event().wait()

    owner.get_agent_business_test_evidence.side_effect = delayed
    requests = AgentBusinessReviewRequests(reviews, review_seconds=0.05)
    with pytest.raises(ConflictError):
        await asyncio.wait_for(
            requests.submit(project, actor, agent, report, command), 2
        )
    state = await requests.state(project, actor, agent, report, command.request_id)
    assert state.status == "failed" and state.error_code == "review_timeout"
    assert await db.scalar(select(AgentBusinessReview.id)) is None


@pytest.mark.asyncio
async def test_newer_review_does_not_rebase_the_old_pending_command(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    await requests.register(project, actor, agent, report, command)
    other = command.model_copy(
        update={"request_id": uuid4(), "decision": "rejected", "reason": "wrong_result"}
    )
    newer = await reviews.review(project, actor, agent, report, other)
    with pytest.raises(ConflictError):
        await requests.resume(project, actor, agent, report, command.request_id)
    state = await requests.state(project, actor, agent, report, command.request_id)
    assert state.status == "failed" and state.command.expected_latest_review_id is None
    assert (await reviews.state(project, actor, agent, report)).latest_review == newer


@pytest.mark.asyncio
async def test_completed_request_preserves_receipt_after_later_rejection(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    first = await reviews.review(project, actor, agent, report, command)
    await reviews.review(
        project,
        actor,
        agent,
        report,
        command.model_copy(
            update={
                "request_id": uuid4(),
                "expected_latest_review_id": first.id,
                "decision": "rejected",
                "reason": "wrong_result",
            }
        ),
    )
    requests = AgentBusinessReviewRequests(reviews)
    assert (
        await requests.cancel(project, actor, agent, report, command.request_id)
    ).result == first
    assert (
        await requests.resume(project, actor, agent, report, command.request_id)
        == first
    )
    assert (
        await reviews.state(project, actor, agent, report)
    ).review_status == "rejected"


@pytest.mark.asyncio
async def test_legacy_receipt_recovers_without_inventing_its_original_command(
    review_fixture,
):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    first = await reviews.review(project, actor, agent, report, command)
    await db.execute(delete(AgentBusinessReviewRequest))
    await db.commit()
    state = await AgentBusinessReviewRequests(reviews).state(
        project, actor, agent, report, command.request_id
    )
    assert (
        state.status == "completed"
        and state.command is None
        and state.result == first
        and not state.can_resume
    )


@pytest.mark.asyncio
async def test_expired_pending_request_after_restart_is_not_resumed(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    row = await requests.register(project, actor, agent, report, command)
    row.deadline_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    state = await AgentBusinessReviewRequests(reviews).state(
        project, actor, agent, report, command.request_id
    )
    assert state.status == "failed" and state.error_code == "review_timeout"
    with pytest.raises(ConflictError):
        await requests.resume(project, actor, agent, report, command.request_id)


@pytest.mark.asyncio
async def test_revoked_reviewer_cannot_read_or_resume_saved_command(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    requests = AgentBusinessReviewRequests(reviews)
    await requests.register(project, actor, agent, report, command)
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("revoked")
    for action in (requests.state, requests.resume, requests.cancel):
        with pytest.raises(AuthorizationError):
            await action(project, actor, agent, report, command.request_id)


@pytest.mark.asyncio
async def test_duplicate_observation_does_not_terminate_a_live_review(review_fixture):
    db, trials, owner, fingerprint, project, actor, agent, report, reviews, command = (
        review_fixture
    )
    entered, release = asyncio.Event(), asyncio.Event()
    previous = owner.get_agent_business_test_evidence.side_effect

    async def delayed(query):
        entered.set()
        await release.wait()
        return previous(query)

    owner.get_agent_business_test_evidence.side_effect = delayed
    task = asyncio.create_task(reviews.review(project, actor, agent, report, command))
    await asyncio.wait_for(entered.wait(), 3)
    async with async_sessionmaker(db.bind, expire_on_commit=False)() as other:
        independent = AgentBusinessReviewService(
            other,
            AgentBusinessTrialService(other, owner=owner, fingerprint=fingerprint),
        )
        requests = AgentBusinessReviewRequests(independent)
        state = await requests.state(project, actor, agent, report, command.request_id)
        assert state.processing and not state.can_resume and state.status == "pending"
        with pytest.raises(ConflictError):
            await independent.review(project, actor, agent, report, command)
        assert (
            await requests.state(project, actor, agent, report, command.request_id)
        ).processing
    release.set()
    result = await task
    assert result.decision == "passed"

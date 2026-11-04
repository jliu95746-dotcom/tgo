"""A real trial report settles one reservation; unknown outcomes never refund blindly."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models.ai_usage import AIUsageMovement, AIUsageReservation
from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services import agent_trial_usage as usage
from app.services import agent_trials, ai_usage_recovery, ai_usage_runtime
from tests.test_ai_usage import prepare
from tests.test_billing_quotes import commercial_company  # noqa: F401


@pytest.fixture
def trial_company(request, monkeypatch):
    db, staff, batch = prepare(request.getfixturevalue("commercial_company"))

    @contextmanager
    def session():
        yield db

    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(settings, "SAAS_ENABLED", True)
    monkeypatch.setattr(settings, "SAAS_BILLING_ENABLED", True)
    monkeypatch.setattr(usage, "SessionLocal", session)
    monkeypatch.setattr(ai_usage_recovery, "SessionLocal", session)
    monkeypatch.setattr(usage.asyncio, "to_thread", immediate)
    return db, staff, batch


def request_report(agent, status="completed", answer="材质是牛皮。"):
    request = AgentTrialRequest(request_key=uuid4(), message="材质是什么？")
    report = AgentTrialReport(
        id=uuid4(), agent_id=agent, request_key=request.request_key,
        status=status, channel="web", question=request.message,
        answer=answer, created_at=datetime.now(timezone.utc),
    )
    return request, report


def test_retry_reuses_original_permit_and_one_charge(trial_company):
    db, staff, batch = trial_company
    agent = uuid4()
    request, report = request_report(agent)
    first = usage.reserve_trial(staff.project_id, staff.id, agent, request)
    second = usage.reserve_trial(staff.project_id, staff.id, agent, request)
    assert first == second and first is not None
    usage.finish_trial(staff.project_id, staff.id, report)
    usage.finish_trial(staff.project_id, staff.id, report)
    assert batch.remaining == 0
    assert db.query(AIUsageReservation).one().status == "settled"
    assert db.query(AIUsageMovement).count() == 2
    assert db.query(AIUsageReservation).one().receipt["kind"] == "agent_trial"


def test_same_key_cannot_change_question_or_agent(trial_company):
    _, staff, _ = trial_company
    agent = uuid4()
    request, _ = request_report(agent)
    usage.reserve_trial(staff.project_id, staff.id, agent, request)
    for identifier, payload in [(agent, request.model_copy(update={"message": "different"})), (uuid4(), request)]:
        with pytest.raises(TGOAPIException) as error:
            usage.reserve_trial(staff.project_id, staff.id, identifier, payload)
        assert error.value.status_code == 409


@pytest.mark.parametrize("status", ["failed", "cancelled", "blocked"])
def test_no_reply_releases_credit_exactly_once(trial_company, status):
    db, staff, batch = trial_company
    agent = uuid4()
    request, report = request_report(agent, status, "")
    usage.reserve_trial(staff.project_id, staff.id, agent, request)
    usage.finish_trial(staff.project_id, staff.id, report)
    usage.finish_trial(staff.project_id, staff.id, report)
    assert batch.remaining == 1
    assert db.query(AIUsageReservation).one().status == "released"
    assert db.query(AIUsageMovement).count() == 2


def test_safe_reply_is_billed_but_does_not_approve_capability(trial_company):
    db, staff, batch = trial_company
    agent = uuid4()
    request, report = request_report(agent, "blocked", "无法确认，请补充资料。")
    usage.reserve_trial(staff.project_id, staff.id, agent, request)
    usage.finish_trial(staff.project_id, staff.id, report)
    assert batch.remaining == 0
    assert db.query(AIUsageReservation).one().status == "settled"
    assert report.can_enable is False and report.assessment == "not_reviewed"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "absent", "unavailable"])
async def test_expired_trial_recovers_from_owned_report(trial_company, monkeypatch, outcome):
    db, staff, batch = trial_company
    agent = uuid4()
    request, report = request_report(agent)
    usage.reserve_trial(staff.project_id, staff.id, agent, request)
    row = db.query(AIUsageReservation).one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    # The ordinary sweep must not refund a trial before checking its report.
    ai_usage_recovery.release_abandoned()
    assert row.status == "reserved" and batch.remaining == 0
    read = AsyncMock(return_value=report)
    if outcome != "completed":
        read.side_effect = HTTPException(404 if outcome == "absent" else 503, "synthetic")
    monkeypatch.setattr(usage.ai_client, "agent_trial_by_request", read)
    await usage.recover_trials()
    assert read.await_args.args == (staff.project_id, staff.id, agent, request.request_key)
    assert row.status == {"completed": "settled", "absent": "released", "unavailable": "review"}[outcome]
    assert batch.remaining == (1 if outcome == "absent" else 0)


@pytest.mark.asyncio
async def test_public_retry_uses_original_report_without_generation(trial_company, monkeypatch):
    _, staff, batch = trial_company
    agent = uuid4()
    request, report = request_report(agent)
    find = AsyncMock(side_effect=[HTTPException(404), report])

    async def generate(*args):
        assert ai_usage_runtime.current_permit() is not None
        return report

    start = AsyncMock(side_effect=generate)
    monkeypatch.setattr(agent_trials.ai_client, "match_agent_trial_request", find)
    monkeypatch.setattr(agent_trials.ai_client, "start_agent_trial", start)
    assert await agent_trials.start(staff.project_id, staff.id, agent, request) == report
    assert await agent_trials.start(staff.project_id, staff.id, agent, request) == report
    assert start.await_count == 1 and batch.remaining == 0
    assert ai_usage_runtime.current_permit() is None


@pytest.mark.asyncio
async def test_no_credit_prevents_generation(trial_company, monkeypatch):
    db, staff, batch = trial_company
    batch.remaining = 0
    db.commit()
    request, _ = request_report(uuid4())
    monkeypatch.setattr(agent_trials.ai_client, "match_agent_trial_request", AsyncMock(side_effect=HTTPException(404)))
    generate = AsyncMock()
    monkeypatch.setattr(agent_trials.ai_client, "start_agent_trial", generate)
    with pytest.raises(TGOAPIException) as error:
        await agent_trials.start(staff.project_id, staff.id, uuid4(), request)
    assert error.value.code == "AI_QUOTA_EXHAUSTED"
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_transport_unknown_keeps_credit_for_recovery(trial_company, monkeypatch):
    db, staff, batch = trial_company
    agent = uuid4()
    request, _ = request_report(agent)
    monkeypatch.setattr(agent_trials.ai_client, "match_agent_trial_request", AsyncMock(side_effect=HTTPException(404)))
    monkeypatch.setattr(agent_trials.ai_client, "start_agent_trial", AsyncMock(side_effect=HTTPException(504)))
    with pytest.raises(HTTPException):
        await agent_trials.start(staff.project_id, staff.id, agent, request)
    assert batch.remaining == 0 and db.query(AIUsageReservation).one().status == "review"

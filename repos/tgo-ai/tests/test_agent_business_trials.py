"""Business test requests remain private, recoverable, and query-purpose limited."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.exceptions import AuthorizationError, ConflictError
from app.models import Agent, BaseModel
from app.models.agent_business_connection import AgentBusinessConnection
from app.models.agent_business_trial import AgentBusinessTrial
from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialCheck,
    BusinessTrialReceipt,
    BusinessTrialCustomerEvidence,
)
from app.schemas.agent_business_connections import BusinessConnectionOwner
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_business_trials import AgentBusinessTrialService
from app.services.agent_business_reviews import AgentBusinessReviewService
from app.schemas.agent_business_reviews import (
    BusinessReviewChecks,
    BusinessReviewRequest,
    BusinessTestEvidence,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["customer_profile_query", "membership_query"])
async def test_verified_customer_query_is_prepared_without_order_and_keeps_test_scope(
    trial, operation
):
    db, service, owner, _, project, actor, agent, previous = trial
    binding = await db.scalar(
        select(AgentBusinessConnection).where(
            AgentBusinessConnection.project_id == project,
            AgentBusinessConnection.agent_id == agent,
        )
    )
    binding.operations = [operation]
    await db.commit()
    original_owner = owner.get_agent_business_connection.side_effect

    def customer_owner(query):
        value = original_owner(query)
        value.operations = [operation]
        value.configuration_tests[0].operation = operation
        return value

    owner.get_agent_business_connection.side_effect = customer_owner
    request = BusinessTrialRequest(
        request_id=uuid4(),
        connection_id=previous.connection_id,
        operation=operation,
        visitor_id=previous.visitor_id,
        customer_session=SecretStr("private-session-token"),
    )
    report, command = await service.prepare(project, actor, agent, request)
    assert command.order_no is None and "order_no" not in command.private_payload()
    await service.authorize(BusinessTrialCheck(command=command, phase="before_query"))
    summary = (
        {"status": "查询完成", "service_tags": []}
        if operation == "customer_profile_query"
        else {"status": "查询完成", "tier": "金卡", "points_balance": 120}
    )
    result = BusinessTrialReceipt(
        **command.identity(),
        outcome="passed",
        summary=summary,
        error_code=None,
        customer_evidence=BusinessTrialCustomerEvidence(
            binding_id=uuid4(),
            binding_revision=1,
            policy_revision=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
        checked_at=datetime.now(UTC),
    )
    await service.authorize(BusinessTrialCheck(command=command, phase="after_query"))
    await service.finish(command, result)
    saved = await service.get(project, actor, agent, report.id)
    assert saved.status == "completed" and saved.receipt.outcome == "passed"
    assert saved.can_enable is False and saved.receipt.can_enable is False
    assert "private-session-token" not in saved.model_dump_json()
    owner.get_agent_business_test_evidence.side_effect = (
        lambda query: BusinessTestEvidence(
            **query.model_dump(),
            status="current",
            issues=[],
            checked_at=datetime.now(UTC),
        )
    )
    reviews = AgentBusinessReviewService(db, service)
    current = await reviews.state(project, actor, agent, report.id)
    assert current.status == "current" and current.can_review
    decision = await reviews.review(
        project,
        actor,
        agent,
        report.id,
        BusinessReviewRequest(
            request_id=uuid4(),
            expected_latest_review_id=None,
            decision="passed",
            reason=None,
            checks=BusinessReviewChecks(
                ownership_checked=True,
                result_checked=True,
                readonly_scope_checked=True,
            ),
        ),
    )
    assert decision.decision == "passed" and decision.can_enable is False


@pytest_asyncio.fixture
async def trial(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'business-trial.sqlite'}"
    )
    async with engine.begin() as db:
        await db.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, agent, connection = [uuid4() for _ in range(4)]
        db.add(
            Agent(
                id=agent,
                project_id=project,
                name="订单测试员工",
                model="fixture",
                is_active=False,
            )
        )
        db.add(
            AgentBusinessConnection(
                project_id=project,
                agent_id=agent,
                connection_id=connection,
                operations=["order_query"],
                enabled=True,
                revision=1,
                configuration_revision=1,
                authorization_revision=1,
                resource_revision="a" * 64,
                configured_by=actor,
            )
        )
        await db.commit()
        owner = AsyncMock()
        owner.get_agent_configuration_actor.side_effect = lambda q: (
            AgentConfigurationActor(**q.model_dump(), checked_at=datetime.now(UTC))
        )
        proof_id = uuid4()
        proof_until = datetime.now(UTC) + timedelta(hours=1)
        owner.get_agent_business_connection.side_effect = lambda q: (
            BusinessConnectionOwner(
                **q.model_dump(),
                name="真实接口",
                mode="http",
                enabled=True,
                operations=["order_query"],
                revision=2,
                configuration_revision=1,
                authorization_revision=1,
                resource_revision="a" * 64,
                test_status="passed",
                checked_at=datetime.now(UTC),
                configuration_tests=[
                    {
                        "operation": "order_query",
                        "report_id": proof_id,
                        "status": "current",
                        "valid_until": proof_until,
                        "issues": [],
                    }
                ],
            )
        )
        fingerprint = AsyncMock(return_value="b" * 64)
        service = AgentBusinessTrialService(db, owner=owner, fingerprint=fingerprint)
        request = BusinessTrialRequest(
            request_id=uuid4(),
            connection_id=connection,
            operation="order_query",
            visitor_id=uuid4(),
            order_no=SecretStr("ORDER-PRIVATE-2026"),
            customer_session=SecretStr("private-session-token"),
        )
        yield db, service, owner, fingerprint, project, actor, agent, request
    await engine.dispose()


@pytest.mark.asyncio
async def test_original_business_request_is_durable_but_contains_no_private_query(
    trial,
):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    first, command = await service.prepare(project, actor, agent, request)
    replay, second = await service.prepare(project, actor, agent, request)
    assert (
        replay.id == first.id
        and second is None
        and first.can_enable is False
        and first.assessment == "not_reviewed"
    )
    row = await db.get(AgentBusinessTrial, first.id)
    stored = repr({key: getattr(row, key) for key in row.__table__.columns.keys()})
    assert "ORDER-PRIVATE-2026" not in stored and "private-session-token" not in stored
    assert "private-session-token" not in repr(
        command
    ) and "ORDER-PRIVATE-2026" not in repr(command)
    assert (
        await service.by_request(project, actor, agent, request.request_id)
    ).id == first.id
    with pytest.raises(ConflictError):
        await service.prepare(
            project,
            actor,
            agent,
            request.model_copy(update={"order_no": SecretStr("OTHER-PRIVATE")}),
        )


@pytest.mark.asyncio
async def test_business_query_claim_is_single_use_and_cancellation_revokes_result(
    trial,
):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    before = await service.authorize(
        BusinessTrialCheck(command=command, phase="before_query")
    )
    assert before.can_enable is False and before.can_query_test is True
    with pytest.raises(ConflictError):
        await service.authorize(
            BusinessTrialCheck(command=command, phase="before_query")
        )
    await service.authorize(BusinessTrialCheck(command=command, phase="after_query"))
    cancelled = await service.cancel(project, actor, agent, report.id)
    assert cancelled.status == "cancelled"
    with pytest.raises(ConflictError):
        await service.authorize(
            BusinessTrialCheck(command=command, phase="after_query")
        )


@pytest.mark.asyncio
async def test_current_configuration_and_actor_are_rechecked_for_each_query(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    fingerprint.return_value = "c" * 64
    with pytest.raises(ConflictError):
        await service.authorize(
            BusinessTrialCheck(command=command, phase="before_query")
        )
    fingerprint.return_value = "b" * 64
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("revoked")
    with pytest.raises(AuthorizationError):
        await service.by_request(project, actor, agent, request.request_id)


@pytest.mark.asyncio
async def test_expired_report_is_not_restarted_by_original_request(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    row = await db.get(AgentBusinessTrial, report.id)
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    expired = await service.by_request(project, actor, agent, request.request_id)
    assert expired.status == "failed" and expired.error_code == "interrupted"
    replay, next_command = await service.prepare(project, actor, agent, request)
    assert replay.id == report.id and next_command is None


def receipt(command, **changes):
    return BusinessTrialReceipt(
        **command.identity(),
        outcome="passed",
        summary={"status": "已发货"},
        error_code=None,
        customer_evidence=BusinessTrialCustomerEvidence(
            binding_id=uuid4(),
            binding_revision=1,
            policy_revision=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        ),
        checked_at=datetime.now(UTC),
        **changes,
    )


@pytest.mark.asyncio
async def test_run_dispatches_once_and_persists_safe_report(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)

    async def query(value):
        await service.authorize(BusinessTrialCheck(command=value, phase="before_query"))
        return receipt(value)

    owner.query_agent_business_trial.side_effect = query
    await service.run(command)
    await service.run(command)
    owner.query_agent_business_trial.assert_awaited_once_with(command)
    saved = await service.get(project, actor, agent, report.id)
    assert (
        saved.status == "completed"
        and saved.receipt.outcome == "passed"
        and not saved.can_enable
    )
    assert request.order_no.get_secret_value() not in saved.model_dump_json()
    assert request.customer_session.get_secret_value() not in saved.model_dump_json()


@pytest.mark.asyncio
async def test_receipt_cannot_complete_unclaimed_query(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    with pytest.raises(ConflictError):
        await service.finish(command, receipt(command))
    assert (await service.get(project, actor, agent, report.id)).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["cancel", "fingerprint", "binding", "expired", "actor"]
)
async def test_late_receipt_never_installs_success_after_revocation(trial, change):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    await service.authorize(BusinessTrialCheck(command=command, phase="before_query"))
    result = receipt(command)
    if change == "cancel":
        await service.cancel(project, actor, agent, report.id)
    if change == "fingerprint":
        fingerprint.return_value = "c" * 64
    if change == "binding":
        from sqlalchemy import update

        await db.execute(update(AgentBusinessConnection).values(enabled=False))
        await db.commit()
    if change == "expired":
        row = await db.get(AgentBusinessTrial, report.id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    if change == "actor":
        owner.get_agent_configuration_actor.side_effect = AuthorizationError("revoked")
    with pytest.raises((ConflictError, AuthorizationError)):
        await service.finish(command, result)
    await db.rollback()
    assert (await db.get(AgentBusinessTrial, report.id)).receipt_json is None


@pytest.mark.asyncio
async def test_network_exception_is_generic_original_result_and_not_automatically_replayed(
    trial,
):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    report, command = await service.prepare(project, actor, agent, request)
    owner.query_agent_business_trial.side_effect = RuntimeError(
        request.customer_session.get_secret_value()
    )
    await service.run(command)
    original, next_command = await service.prepare(project, actor, agent, request)
    assert (
        original.id == report.id
        and next_command is None
        and original.error_code == "result_unknown"
    )
    assert "private-session-token" not in original.model_dump_json()


@pytest.mark.asyncio
async def test_another_request_cannot_overlap_same_employees_pending_test(trial):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    first, command = await service.prepare(project, actor, agent, request)
    with pytest.raises(ConflictError):
        await service.prepare(
            project, actor, agent, request.model_copy(update={"request_id": uuid4()})
        )
    await service.cancel(project, actor, agent, first.id)
    second, next_command = await service.prepare(
        project, actor, agent, request.model_copy(update={"request_id": uuid4()})
    )
    assert second.id != first.id and next_command is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["active", "demo", "disabled", "missing_test", "operation"]
)
async def test_only_paused_employee_current_http_operation_can_prepare(trial, change):
    db, service, owner, fingerprint, project, actor, agent, request = trial
    if change == "active":
        row = await db.get(Agent, agent)
        row.is_active = True
        await db.commit()
    else:
        original = owner.get_agent_business_connection.side_effect

        def changed(q):
            value = original(q).model_dump()
            if change == "demo":
                value["mode"] = "demo"
            if change == "disabled":
                value["enabled"] = False
            if change == "missing_test":
                value["configuration_tests"][0].update(
                    status="not_tested", report_id=None, valid_until=None
                )
            if change == "operation":
                value["operations"] = ["logistics_query"]
                value["configuration_tests"][0]["operation"] = "logistics_query"
            return BusinessConnectionOwner(**value)

        owner.get_agent_business_connection.side_effect = changed
    with pytest.raises(ConflictError):
        await service.prepare(project, actor, agent, request)

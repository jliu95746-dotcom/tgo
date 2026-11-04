"""A switch and its immutable receipt succeed or roll back together."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.exceptions import AuthorizationError, ConflictError
from app.models import Agent, AgentActivationRequestRecord, BaseModel, Project
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.schemas.agent_activation_requests import AgentActivationRequest
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_activation_readiness import AgentActivationReadinessService
from app.services.agent_activation_requests import AgentActivationRequestService


@pytest_asyncio.fixture
async def fixture(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(BaseModel.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        project, actor, old_id, target_id = [uuid4() for _ in range(4)]
        db.add(
            Project(id=project, name="启用回执隔离", api_key="synthetic_" + uuid4().hex)
        )
        db.add_all(
            [
                Agent(
                    id=old_id,
                    project_id=project,
                    name="旧接待",
                    model="fixture",
                    is_active=True,
                    is_default=True,
                    skills_enabled=False,
                ),
                Agent(
                    id=target_id,
                    project_id=project,
                    name="待启用",
                    model="fixture",
                    is_active=False,
                    is_default=False,
                    skills_enabled=False,
                ),
            ]
        )
        await db.commit()
        owner = AsyncMock()
        owner.get_agent_configuration_actor.side_effect = lambda query: (
            AgentConfigurationActor(**query.model_dump(), checked_at=datetime.now(UTC))
        )
        evidence = AgentActivationReadiness.model_validate(
            {
                "project_id": project,
                "agent_id": target_id,
                "checked_by": actor,
                "checked_at": datetime.now(UTC),
                "enabled": False,
                "can_enable": True,
                "configuration_fingerprint": "a" * 64,
                "model_owner_revision": "b" * 64,
                "channels": [
                    {
                        "template_id": "knowledge",
                        "template_version": 1,
                        "channel": "web",
                        "policy_revision": 1,
                        "requirements_revision": "c" * 64,
                        "status": "passed",
                        "valid_until": datetime.now(UTC) + timedelta(hours=1),
                    }
                ],
            }
        )
        read = AsyncMock(return_value=evidence)
        monkeypatch.setattr(AgentActivationReadinessService, "read", read)
        command = AgentActivationRequest.model_validate(
            {
                "request_key": uuid4(),
                "expected": {
                    "checked_at": evidence.checked_at,
                    "configuration_fingerprint": evidence.configuration_fingerprint,
                    "model_owner_revision": evidence.model_owner_revision,
                    "channels": [
                        row.model_dump(exclude={"status", "valid_until", "business_capabilities"})
                        for row in evidence.channels
                    ],
                },
            }
        )
        yield (
            db,
            AgentActivationRequestService(db, owner),
            owner,
            project,
            actor,
            old_id,
            target_id,
            command,
            evidence,
            read,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_switch_and_receipt_are_atomic_and_retries_do_not_switch_again(fixture):
    db, service, _, project, actor, old_id, target, command, _, read = fixture
    first = await service.submit(project, actor, target, command)
    assert first.status == "completed" and first.can_enable is False
    assert (await db.get(Agent, target)).is_active is True and (
        await db.get(Agent, old_id)
    ).is_active is False
    before = read.await_count
    repeated = await service.submit(project, actor, target, command)
    assert repeated == first and read.await_count == before
    assert await service.lookup(project, actor, target, command.request_key) == first


@pytest.mark.asyncio
async def test_same_key_different_intent_is_conflict_without_a_second_effect(fixture):
    _, service, _, project, actor, _, target, command, _, _ = fixture
    await service.submit(project, actor, target, command)
    changed = command.model_copy(deep=True)
    changed.expected.configuration_fingerprint = "d" * 64
    with pytest.raises(ConflictError):
        await service.submit(project, actor, target, changed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "configuration",
        "model",
        "policy",
        "requirements",
        "channel",
        "not_ready",
        "expired",
    ],
)
async def test_changed_or_invalid_confirmation_has_terminal_rejection_and_preserves_old_employee(
    fixture, change
):
    db, service, _, project, actor, old_id, target, command, evidence, _ = fixture
    if change == "configuration":
        evidence.configuration_fingerprint = "d" * 64
    if change == "model":
        evidence.model_owner_revision = "d" * 64
    if change == "policy":
        evidence.channels[0].policy_revision = 2
    if change == "requirements":
        evidence.channels[0].requirements_revision = "d" * 64
    if change == "channel":
        evidence.channels[0].channel = evidence.channels[0].channel.__class__.WECOM_KF
    if change == "not_ready":
        evidence.can_enable = False
    if change == "expired":
        command.expected.checked_at -= timedelta(minutes=5)
    result = await service.submit(project, actor, target, command)
    assert result.status == "rejected" and result.failure_code
    assert (await db.get(Agent, old_id)).is_active is True and (
        await db.get(Agent, target)
    ).is_active is False
    assert await service.submit(project, actor, target, command) == result


@pytest.mark.asyncio
async def test_role_revocation_denies_replay_and_lookup(fixture):
    _, service, owner, project, actor, _, target, command, _, _ = fixture
    await service.submit(project, actor, target, command)
    owner.get_agent_configuration_actor.side_effect = AuthorizationError("权限已撤销")
    for action in [
        service.submit(project, actor, target, command),
        service.lookup(project, actor, target, command.request_key),
    ]:
        with pytest.raises(AuthorizationError):
            await action


@pytest.mark.asyncio
async def test_missing_receipt_is_not_a_claim_that_the_original_operation_stopped(
    fixture,
):
    _, service, _, project, actor, _, target, command, _, _ = fixture
    result = await service.lookup(project, actor, target, command.request_key)
    assert (
        result.status == "not_found"
        and result.command is None
        and result.finished_at is None
    )
    assert (await service.submit(project, actor, target, command)).status == "completed"


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_switch_and_receipt(fixture, monkeypatch):
    db, service, _, project, actor, old_id, target, command, _, _ = fixture
    commit = db.commit
    monkeypatch.setattr(
        db, "commit", AsyncMock(side_effect=RuntimeError("synthetic commit failure"))
    )
    with pytest.raises(RuntimeError):
        await service.submit(project, actor, target, command)
    monkeypatch.setattr(db, "commit", commit)
    assert (await db.get(Agent, old_id)).is_active is True and (
        await db.get(Agent, target)
    ).is_active is False
    assert not (await db.scalars(select(AgentActivationRequestRecord))).all()


@pytest.mark.asyncio
async def test_cancellation_leaves_neither_receipt_nor_half_switch(fixture):
    db, service, _, project, actor, old_id, target, command, _, read = fixture
    started, release = asyncio.Event(), asyncio.Event()

    async def wait(*args, **kwargs):
        started.set()
        await release.wait()

    read.side_effect = wait
    task = asyncio.create_task(service.submit(project, actor, target, command))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await db.get(Agent, old_id)).is_active is True and (
        await db.get(Agent, target)
    ).is_active is False
    assert not (await db.scalars(select(AgentActivationRequestRecord))).all()

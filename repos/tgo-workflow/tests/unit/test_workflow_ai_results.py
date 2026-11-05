"""Saved answers recover one original call, with no plaintext in the run row."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.execution import WorkflowExecution
from app.models.workflow import Workflow
from app.schemas.ai_usage import (
    WorkflowAICacheCommand,
    WorkflowAICacheWrite,
    WorkflowAIResult,
)
from app.services import workflow_ai_results as results
from app.services import workflow_ai_result_crypto as crypto
from app.services.execution_snapshot import definition_digest


@pytest_asyncio.fixture
async def case(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project, workflow, execution, agent = [str(uuid4()) for _ in range(4)]
    graph = {
        "nodes": [
            {
                "id": "answer-agent",
                "type": "agent",
                "data": {
                    "agent_id": agent,
                    "reference_key": "reply",
                },
            }
        ],
        "edges": [],
    }
    run = WorkflowExecution(
        id=execution,
        project_id=project,
        workflow_id=workflow,
        status="running",
        workflow_version=1,
        execution_mode="draft_test",
        definition_snapshot=graph,
        definition_digest=definition_digest(graph),
        ai_usage_owner={"staff_id": str(uuid4()), "staff_token_version": 1},
    )
    monkeypatch.setattr(
        results.settings,
        "SECRET_KEY",
        SecretStr("synthetic-recovery-secret-key-20261105"),
    )
    monkeypatch.setattr(results.settings, "WORKFLOW_AI_RESULT_KEYS", None)
    monkeypatch.setattr(
        results.settings, "WORKFLOW_AI_RESULT_ACTIVE_KEY_ID", None
    )
    async with factory() as db:
        db.add(
            Workflow(
                id=workflow,
                project_id=project,
                name="Synthetic",
                definition=graph,
            )
        )
        await db.flush()
        db.add(run)
        await db.commit()
        command = WorkflowAICacheCommand(
            command={
                "project_id": project,
                "execution_id": execution,
                "node_id": "answer-agent",
                "payload": {
                    "agent_id": agent,
                    "message": "private-question-not-for-storage",
                },
            },
            reservation_id=uuid4(),
            lease_id=uuid4(),
        )
        yield db, run, command
    await engine.dispose()


def answer(command):
    return WorkflowAIResult(
        success=True,
        content="private-answer-not-for-logs",
        execution_id=uuid4(),
        execution_reference={
            "project_id": command.command.project_id,
            "agent_id": command.command.payload.agent_id,
            "channel": "internal",
            "configuration_fingerprint": "a" * 64,
            "policy_revision": "b" * 64,
        },
    )


@pytest.mark.asyncio
async def test_prepare_claims_once_and_result_is_encrypted(case):
    db, run, command = case
    first = await results.prepare(db, command)
    assert first.state == "claimed" and first.result is None
    second = await results.prepare(db, command)
    assert second.state == "pending" and second.claim_id == first.claim_id
    result = answer(command)
    stored = await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=result,
        ),
    )
    assert stored.state == "completed" and stored.result == result
    assert await results.read(db, command) == stored
    assert (await results.prepare(db, command)).result == result
    encoded = str(run.ai_result_receipts)
    assert result.content not in encoded
    assert command.command.payload.message not in encoded
    assert results.settings.SECRET_KEY.get_secret_value() not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["message", "reservation", "lease", "owner", "definition"]
)
async def test_saved_result_cannot_change_its_scope(case, change):
    db, run, command = case
    first = await results.prepare(db, command)
    await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=answer(command),
        ),
    )
    if change == "message":
        inner = command.command.model_copy(
            update={
                "payload": command.command.payload.model_copy(
                    update={"message": "different question"}
                )
            }
        )
        command = command.model_copy(update={"command": inner})
    elif change in {"reservation", "lease"}:
        command = command.model_copy(update={change + "_id": uuid4()})
    elif change == "owner":
        run.ai_usage_owner = {"unmetered": True}
        await db.commit()
    else:
        run.definition_digest = "0" * 64
        await db.commit()
    with pytest.raises(HTTPException):
        await results.read(db, command)


@pytest.mark.asyncio
async def test_same_claim_cannot_overwrite_an_original_answer(case):
    db, _, command = case
    first = await results.prepare(db, command)
    original = WorkflowAICacheWrite(
        **command.model_dump(), claim_id=first.claim_id, result=answer(command)
    )
    await results.store(db, original)
    assert (await results.store(db, original)).result == original.result
    with pytest.raises(HTTPException):
        await results.store(
            db, original.model_copy(update={"result": answer(command)})
        )
    with pytest.raises(HTTPException):
        await results.store(
            db, original.model_copy(update={"claim_id": uuid4()})
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_cleanup_erases_body_and_preserves_no_reexecution_marker(
    case, terminal
):
    db, run, command = case
    first = await results.prepare(db, command)
    result = answer(command)
    await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=result,
        ),
    )
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    if terminal:
        run.status = "cancelled"
        await db.commit()
    assert await results.purge(db, now=future) == 1
    await db.refresh(run)
    assert "ciphertext" not in str(run.ai_result_receipts)
    assert run.ai_result_expires_at is None
    if terminal:
        run.status = (
            "running"  # Even an invalid state reset cannot regenerate.
        )
        await db.commit()
    with pytest.raises(HTTPException):
        await results.prepare(db, command)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["completed", "failed", "cancelled", "waiting_approval"]
)
async def test_closed_or_waiting_run_cannot_return_a_cached_answer(
    case, status
):
    db, run, command = case
    first = await results.prepare(db, command)
    await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=answer(command),
        ),
    )
    run.status = status
    await db.commit()
    with pytest.raises(HTTPException):
        await results.read(db, command)


@pytest.mark.asyncio
async def test_key_rotation_requires_retained_original_key(case, monkeypatch):
    db, _, command = case
    first = await results.prepare(db, command)
    original = answer(command)
    await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=original,
        ),
    )
    legacy = results.settings.SECRET_KEY.get_secret_value()
    keys = {"legacy": legacy, "next": "synthetic-next-key-material-20261105"}
    monkeypatch.setattr(
        results.settings,
        "WORKFLOW_AI_RESULT_KEYS",
        SecretStr(json.dumps(keys)),
    )
    monkeypatch.setattr(
        results.settings, "WORKFLOW_AI_RESULT_ACTIVE_KEY_ID", "next"
    )
    assert crypto.active_key_id() == "next"
    assert (await results.read(db, command)).result == original
    keys.pop("legacy")
    monkeypatch.setattr(
        results.settings,
        "WORKFLOW_AI_RESULT_KEYS",
        SecretStr(json.dumps(keys)),
    )
    with pytest.raises(HTTPException) as error:
        await results.read(db, command)
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_tampered_ciphertext_never_returns_an_answer(case):
    db, run, command = case
    first = await results.prepare(db, command)
    await results.store(
        db,
        WorkflowAICacheWrite(
            **command.model_dump(),
            claim_id=first.claim_id,
            result=answer(command),
        ),
    )
    envelope = json.loads(json.dumps(run.ai_result_receipts))
    saved = envelope["calls"][command.command.node_id]
    cipher = saved["ciphertext"]
    saved["ciphertext"] = (
        cipher[:12] + ("A" if cipher[12] != "A" else "B") + cipher[13:]
    )
    run.ai_result_receipts = envelope
    await db.commit()
    with pytest.raises(HTTPException) as error:
        await results.read(db, command)
    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_bad_metadata_is_purged_into_a_non_reexecutable_tombstone(case):
    db, run, command = case
    run.ai_result_receipts = {"private": "synthetic invalid body"}
    run.ai_result_expires_at = datetime.now(timezone.utc) - timedelta(
        minutes=1
    )
    await db.commit()
    assert await results.purge(db) == 1
    assert run.ai_result_receipts == {"state": "purged_invalid"}
    with pytest.raises(HTTPException):
        await results.prepare(db, command)


@pytest.mark.asyncio
async def test_capacity_failure_does_not_leave_a_partial_claim(
    case, monkeypatch
):
    db, run, command = case
    monkeypatch.setattr(results, "MAX_RECEIPT_BYTES", 1)
    with pytest.raises(HTTPException) as error:
        await results.prepare(db, command)
    assert error.value.status_code == 413
    await db.rollback()
    await db.refresh(run)
    assert run.ai_result_receipts is None


@pytest.mark.parametrize("fault", ["missing", "project", "agent", "channel"])
@pytest.mark.asyncio
async def test_storage_rejects_a_foreign_or_unproven_producer(case, fault):
    _, _, command = case
    result = answer(command).model_dump(mode="json")
    if fault == "missing":
        result["execution_reference"] = None
    else:
        result["execution_reference"][
            fault if fault == "channel" else fault + "_id"
        ] = ("web" if fault == "channel" else str(uuid4()))
    with pytest.raises(ValueError):
        WorkflowAICacheWrite(
            **command.model_dump(), claim_id=uuid4(), result=result
        )

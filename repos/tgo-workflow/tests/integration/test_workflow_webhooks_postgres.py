"""Real PostgreSQL locking, migration and credential command recovery."""

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import HTTPException
import httpx
from pydantic import SecretStr
import pytest
from sqlalchemy import delete, func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.models.workflow_webhook import WorkflowWebhook, WorkflowWebhookCommand
from app.schemas.workflow_webhook import WebhookChange
from app.services import workflow_webhooks as hooks
from app.services.execution_snapshot import definition_digest
from tests.integration import test_workflow_schedules_postgres as pgfixtures
from tests.unit.test_workflow_webhooks import (
    Fixture,
    definition,
    request,
    save,
)

database = pgfixtures.database

pytestmark = pytest.mark.skipif(
    not os.getenv("WORKFLOW_POSTGRES_TEST_URL"),
    reason="An explicitly disposable loopback PostgreSQL database is required",
)


@pytest.fixture(autouse=True)
def synthetic_master(monkeypatch):
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(
            json.dumps(
                {
                    "first": "synthetic-webhook-master-key-1234567890",
                }
            )
        ),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "first")


async def seed(engine, project=None):
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project, actor = project or uuid4(), uuid4()
    flow, release = str(uuid4()), str(uuid4())
    graph, digest = definition(), definition_digest(definition())
    async with sessions() as db:
        db.add(
            Workflow(
                id=flow,
                project_id=str(project),
                name="隔离外部触发",
                definition=graph,
                status="active",
                version=1,
                published_version=1,
                publication_revision=1,
            )
        )
        await db.flush()
        db.add(
            WorkflowRelease(
                id=release,
                workflow_id=flow,
                project_id=str(project),
                version=1,
                draft_version=1,
                definition=graph,
                definition_digest=digest,
            )
        )
        await db.commit()
    return Fixture(sessions, project, actor, flow, release, digest)


@pytest.mark.asyncio
async def test_identical_concurrent_create_recovers_one_credential(database):
    fixture = await seed(database[0])
    req = request(fixture)
    results = await asyncio.gather(save(fixture, req), save(fixture, req))
    assert sorted(result.replayed for result in results) == [False, True]
    assert results[0].credential == results[1].credential
    async with fixture.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookCommand)
            )
            == 1
        )


@pytest.mark.asyncio
async def test_different_concurrent_create_has_one_revision_winner(database):
    fixture = await seed(database[0])
    results = await asyncio.gather(
        save(fixture, request(fixture)),
        save(fixture, request(fixture)),
        return_exceptions=True,
    )
    winners = [
        result for result in results if not isinstance(result, BaseException)
    ]
    losers = [
        result for result in results if isinstance(result, HTTPException)
    ]
    assert len(winners) == len(losers) == 1
    assert losers[0].status_code == 409
    async with fixture.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 1
        )


@pytest.mark.asyncio
async def test_identical_rotation_is_committed_once(database):
    fixture = await seed(database[0])
    await save(fixture, request(fixture))
    req = WebhookChange(request_id=uuid4(), expected_revision=1)

    async def rotate():
        async with fixture.sessions() as db:
            return await hooks.rotate_webhook(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                req,
            )

    first, repeated = await asyncio.gather(rotate(), rotate())
    assert first.credential == repeated.credential
    assert (
        first.webhook.credential_revision
        == repeated.webhook.credential_revision
        == 2
    )
    assert first.replayed != repeated.replayed


@pytest.mark.asyncio
async def test_rebind_and_rotation_cannot_overwrite_each_other(database):
    fixture = await seed(database[0])
    await save(fixture, request(fixture))

    async def rotate():
        async with fixture.sessions() as db:
            return await hooks.rotate_webhook(
                db,
                fixture.workflow_id,
                fixture.project_id,
                fixture.actor_id,
                WebhookChange(request_id=uuid4(), expected_revision=1),
            )

    results = await asyncio.gather(
        save(fixture, request(fixture, revision=1)),
        rotate(),
        return_exceptions=True,
    )
    winners = [
        result for result in results if not isinstance(result, BaseException)
    ]
    losers = [
        result for result in results if isinstance(result, HTTPException)
    ]
    assert len(winners) == len(losers) == 1 and losers[0].status_code == 409
    async with fixture.sessions() as db:
        row = await db.scalar(select(WorkflowWebhook))
        assert row.revision == 2
        assert (
            row.credential_revision == winners[0].webhook.credential_revision
        )


@pytest.mark.asyncio
async def test_same_request_on_different_flows_cannot_leak_a_credential(
    database,
):
    first = await seed(database[0])
    second = await seed(database[0], first.project_id)
    key = uuid4()
    results = await asyncio.gather(
        save(first, request(first, request_id=key)),
        save(second, request(second, request_id=key)),
        return_exceptions=True,
    )
    winners = [
        result for result in results if not isinstance(result, BaseException)
    ]
    losers = [
        result for result in results if isinstance(result, HTTPException)
    ]
    assert len(winners) == len(losers) == 1 and losers[0].status_code == 409
    async with first.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 1
        )
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookCommand)
            )
            == 1
        )


def config(connection):
    root = Path(__file__).parents[2]
    result = Config(str(root / "alembic.ini"))
    result.set_main_option("script_location", str(root / "migrations"))
    result.attributes["connection"] = connection
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("keep_binding", [True, False])
async def test_nonempty_downgrade_cannot_discard_binding_or_command(
    database, keep_binding
):
    fixture = await seed(database[0])
    await save(fixture, request(fixture))
    if not keep_binding:
        async with fixture.sessions() as db:
            await db.execute(delete(WorkflowWebhook))
            await db.commit()
    with pytest.raises(RuntimeError, match="请求回执"):
        async with database[0].begin() as db:
            await db.run_sync(
                lambda sync: command.downgrade(
                    config(sync), "0019_timer_occurrences"
                )
            )
    async with fixture.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookCommand)
            )
            == 1
        )
        assert await db.scalar(select(func.count()).select_from(Workflow)) == 1


@pytest.mark.asyncio
async def test_empty_down_and_up_preserves_workflow_and_matches_model(
    database,
):
    fixture = await seed(database[0])
    async with database[0].begin() as db:
        await db.run_sync(
            lambda sync: command.downgrade(
                config(sync), "0019_timer_occurrences"
            )
        )
        await db.run_sync(lambda sync: command.upgrade(config(sync), "head"))
        actual = await db.run_sync(
            lambda sync: inspect(sync).get_columns("wf_workflow_webhooks")
        )
        assert {column["name"] for column in actual} == set(
            WorkflowWebhook.__table__.columns.keys()
        )
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Workflow)) == 1
        assert (
            await db.scalar(select(func.count()).select_from(WorkflowWebhook))
            == 0
        )


@pytest.mark.asyncio
async def test_real_private_http_registration_rotation_and_recovery(
    database,
    tmp_path,
):
    fixture = await seed(database[0])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=settings.DATABASE_URL,
        WORKFLOW_TEST_SCHEMA=database[1],
        SECRET_KEY="synthetic-private-webhook-transport",
        WORKFLOW_WEBHOOK_KEYS=json.dumps(
            {
                "test": "synthetic-http-webhook-master-key-1234567890",
            }
        ),
        WORKFLOW_WEBHOOK_ACTIVE_KEY_ID="test",
        PYTHONUTF8="1",
    )
    # Only database routing changes: real app, routes and services stay intact.
    bootstrap = """
import os, sys
import uvicorn
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings
import app.database as database
test_schema = os.environ['WORKFLOW_TEST_SCHEMA']
database.engine = create_async_engine(settings.DATABASE_URL,
    connect_args={'server_settings': {'search_path': test_schema}},
    hide_parameters=True)
database.AsyncSessionLocal.configure(bind=database.engine)
uvicorn.run('app.main:app', host='127.0.0.1', port=int(sys.argv[1]),
    access_log=False, log_level='warning')
"""
    root = Path(__file__).parents[2]
    with (tmp_path / "workflow-http.log").open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-c", bootstrap, str(port)],
            cwd=root,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                trust_env=False,
                timeout=10,
            ) as client:
                deadline = time.monotonic() + 60
                while True:
                    try:
                        health = await client.get("/health")
                        if health.status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    assert process.poll() is None, "Private HTTP server exited"
                    assert (
                        time.monotonic() < deadline
                    ), "Private HTTP startup timed out"
                    await asyncio.sleep(0.2)
                path = f"/v1/workflows/{fixture.workflow_id}/webhook"
                body = request(fixture).model_dump(mode="json")
                body.update(
                    project_id=str(fixture.project_id),
                    actor_staff_id=str(fixture.actor_id),
                )
                headers = {"X-Internal-API-Key": environment["SECRET_KEY"]}
                denied = await client.put(path, json=body)
                assert denied.status_code == 403
                first = await client.put(path, json=body, headers=headers)
                assert first.status_code == 200
                assert first.headers["cache-control"] == "no-store"
                original = first.json()["credential"]["secret"]
                repeated = await client.put(path, json=body, headers=headers)
                assert repeated.json()["replayed"]
                assert repeated.json()["credential"]["secret"] == original
                state = await client.get(
                    path,
                    headers=headers,
                    params={"project_id": str(fixture.project_id)},
                )
                assert state.status_code == 200 and original not in state.text
                assert not state.json()["execution_available"]
                rotation = dict(
                    request_id=str(uuid4()),
                    expected_revision=1,
                    project_id=str(fixture.project_id),
                    actor_staff_id=str(fixture.actor_id),
                )
                rotated = await client.post(
                    path + "/rotate", json=rotation, headers=headers
                )
                assert rotated.status_code == 200
                assert rotated.json()["credential"]["secret"] != original
                old = await client.get(
                    path + "/commands/" + body["request_id"],
                    headers=headers,
                    params={
                        "project_id": str(fixture.project_id),
                        "actor_staff_id": str(fixture.actor_id),
                    },
                )
                assert old.json()["credential_status"] == "superseded"
                assert old.json()["credential"] is None
                hidden = await client.get(
                    path, headers=headers, params={"project_id": str(uuid4())}
                )
                assert hidden.status_code == 404
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

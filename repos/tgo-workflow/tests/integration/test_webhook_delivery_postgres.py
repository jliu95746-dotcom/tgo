"""Durable ingress races and public HTTP -> API authority -> real Celery."""

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
from unittest.mock import AsyncMock
from urllib.parse import urlparse
from uuid import uuid4

from alembic import command
from fastapi import HTTPException
import httpx
from pydantic import SecretStr
import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select

from app.config import settings
from app.models.execution import WorkflowExecution, NodeExecution
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery
from app.services import webhook_ingress as ingress, workflow_webhooks as hooks
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from tests.integration import test_workflow_schedules_postgres as pgfixtures
from tests.integration.test_workflow_webhooks_postgres import seed, config
from tests.unit.test_workflow_webhooks import request

database = pgfixtures.database
pytestmark = pytest.mark.skipif(
    not os.getenv("WORKFLOW_POSTGRES_TEST_URL"),
    reason="Disposable loopback PostgreSQL required",
)
MASTER = "synthetic-http-delivery-master-key-1234567890"
SERVICE_KEY = "synthetic-api-webhook-service-key-1234567890"


async def setup(engine, monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_EXECUTION_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "WORKFLOW_WEBHOOK_KEYS",
        SecretStr(json.dumps({"test": MASTER})),
    )
    monkeypatch.setattr(settings, "WORKFLOW_WEBHOOK_ACTIVE_KEY_ID", "test")
    monkeypatch.setattr(hooks, "verify_webhook_authority", AsyncMock())
    monkeypatch.setattr(ingress, "verify_webhook_authority", AsyncMock())
    fixture = await seed(engine)
    async with fixture.sessions() as db:
        release = await db.get(WorkflowRelease, fixture.release_id)
        graph = copy_json_object(release.definition)
        graph["nodes"][1]["data"][
            "output_template"
        ] = "{{source.body.question}}"
        fixture.digest = definition_digest(graph)
        release.definition, release.definition_digest = graph, fixture.digest
        workflow = await db.get(Workflow, fixture.workflow_id)
        workflow.definition = graph
        await db.commit()
        receipt = await hooks.save_webhook(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture),
        )
        await hooks.save_webhook(
            db,
            fixture.workflow_id,
            fixture.project_id,
            fixture.actor_id,
            request(fixture, revision=1, enabled=True),
        )
    return fixture, receipt


def signed(
    receipt, event="synthetic-001", question="synthetic-private-notification"
):
    body = json.dumps(
        {"data": {"question": question}}, separators=(",", ":")
    ).encode()
    stamp, key = str(int(time.time())), receipt.webhook.key_id
    message = (
        f"TGO-WEBHOOK-V1\n{receipt.webhook.id}\n1\n{key}\n{stamp}\n{event}\n"
    ).encode() + body
    proof = hmac.new(
        receipt.credential.secret.get_secret_value().encode(),
        message,
        hashlib.sha256,
    ).hexdigest()
    return body, dict(
        timestamp=stamp,
        event_id=event,
        key_id=key,
        signature="sha256=" + proof,
    )


async def accept(fixture, receipt, **changes):
    body, headers = signed(receipt, **changes)
    async with fixture.sessions() as db:
        return await ingress.accept_delivery(
            db,
            receipt.webhook.id,
            body=body,
            headers=headers,
            content_type="application/json",
        )


@pytest.mark.asyncio
async def test_concurrent_signed_duplicates_commit_one_delivery(
    database, monkeypatch
):
    fixture, receipt = await setup(database[0], monkeypatch)
    results = await asyncio.gather(
        *(accept(fixture, receipt) for _ in range(4))
    )
    assert sum(not result.deduplicated for result in results) == 1
    assert len({result.delivery_id for result in results}) == 1


@pytest.mark.asyncio
async def test_changed_payload_race_has_one_winner(database, monkeypatch):
    fixture, receipt = await setup(database[0], monkeypatch)
    results = await asyncio.gather(
        accept(fixture, receipt, question="first"),
        accept(fixture, receipt, question="second"),
        return_exceptions=True,
    )
    assert (
        sum(
            isinstance(result, HTTPException) and result.status_code == 409
            for result in results
        )
        == 1
    )
    assert (
        sum(not isinstance(result, BaseException) for result in results) == 1
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_notifications_always_prevent_destructive_downgrade(
    database, monkeypatch, terminal
):
    fixture, receipt = await setup(database[0], monkeypatch)
    created = await accept(fixture, receipt)
    if terminal:
        async with fixture.sessions() as db:
            row = await db.get(
                WorkflowWebhookDelivery, str(created.delivery_id)
            )
            row.status, row.payload_ciphertext = "cancelled", None
            await db.commit()
    with pytest.raises(RuntimeError, match="外部通知来源"):
        async with database[0].begin() as db:
            await db.run_sync(
                lambda sync: command.downgrade(
                    config(sync), "0020_workflow_webhooks"
                )
            )
    async with fixture.sessions() as db:
        assert (
            await db.scalar(
                select(func.count()).select_from(WorkflowWebhookDelivery)
            )
            == 1
        )


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.mark.asyncio
async def test_public_http_durable_recovery_worker_restart_and_revocation(
    database, monkeypatch, tmp_path
):
    assert urlparse(settings.REDIS_URL).hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    fixture, receipt = await setup(database[0], monkeypatch)
    prefix = "webhook-test-" + uuid4().hex + ":"
    queue = "webhook-test-" + uuid4().hex
    api_port, workflow_port = free_port(), free_port()
    service = Path(__file__).parents[2]
    root = service.parents[1]
    bootstrap = Path(__file__).with_name("webhook_process_bootstrap.py")
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=settings.DATABASE_URL,
        REDIS_URL=settings.REDIS_URL,
        WEBHOOK_TEST_SCHEMA=database[1],
        WEBHOOK_TEST_PREFIX=prefix,
        WEBHOOK_TEST_API_DB=str(tmp_path / "api-identity.sqlite"),
        WEBHOOK_TEST_PROJECT=str(fixture.project_id),
        WEBHOOK_TEST_ACTOR=str(fixture.actor_id),
        WEBHOOK_TEST_PASSWORD=secrets.token_urlsafe(32),
        WEBHOOK_EXECUTION_ENABLED="true",
        TIMER_EXECUTION_ENABLED="false",
        SECRET_KEY=SERVICE_KEY,
        WORKFLOW_WEBHOOK_KEYS=json.dumps({"test": MASTER}),
        WORKFLOW_WEBHOOK_ACTIVE_KEY_ID="test",
        WORKFLOW_QUEUE_NAME=queue,
        WORKFLOW_SERVICE_URL=f"http://127.0.0.1:{workflow_port}",
        API_INTERNAL_SERVICE_URL=f"http://127.0.0.1:{api_port}",
        SAAS_ENABLED="false",
        SAAS_SMTP_HOST="",
        SAAS_BILLING_ENABLED="false",
        WUKONGIM_ENABLED="false",
        PYTHONUTF8="1",
    )
    processes, logs, extra_hooks = [], [], []

    def spawn(role, port=0):
        directory = root / "repos/tgo-api" if role == "api" else service
        python = directory / ".venv/Scripts/python.exe"
        log = (tmp_path / (role + "-" + uuid4().hex + ".log")).open("wb")
        logs.append(log)
        process = subprocess.Popen(
            [str(python), str(bootstrap), role, "--port", str(port)],
            cwd=directory,
            env=environment,
            stdout=log,
            stderr=log,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        processes.append(process)
        return process

    redis = Redis.from_url(settings.REDIS_URL, socket_timeout=2)

    async def ready(url, process):
        deadline = time.monotonic() + 60
        async with httpx.AsyncClient(trust_env=False, timeout=2) as client:
            while True:
                try:
                    if (await client.get(url + "/health")).status_code == 200:
                        return
                except httpx.RequestError:
                    pass
                assert process.poll() is None, "Owned test listener exited"
                assert (
                    time.monotonic() < deadline
                ), "Owned test listener startup timed out"
                await asyncio.sleep(0.2)

    async def completed(identifier):
        deadline = time.monotonic() + 150
        while True:
            async with fixture.sessions() as db:
                row = await db.get(WorkflowWebhookDelivery, identifier)
                if row.status == "completed":
                    run = await db.get(WorkflowExecution, row.execution_id)
                    assert run.status == "completed" and run.input is None
                    assert row.payload_ciphertext is None
                    assert "synthetic-private-notification" in json.dumps(
                        run.output
                    )
                    return
                assert row.status in {
                    "pending",
                    "queued",
                }, "Owned notification failed"
            assert (
                time.monotonic() < deadline
            ), "Owned notification did not complete"
            await asyncio.sleep(0.2)

    try:
        api = spawn("api", api_port)
        workflow = spawn("workflow", workflow_port)
        await asyncio.gather(
            ready(f"http://127.0.0.1:{api_port}", api),
            ready(f"http://127.0.0.1:{workflow_port}", workflow),
        )
        worker = spawn("worker")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{api_port}",
            trust_env=False,
            timeout=15,
        ) as client:
            credential = await client.post(
                "/v1/staff/login",
                data={"username": prefix[:-1],
                      "password": environment["WEBHOOK_TEST_PASSWORD"]},
            )
            assert credential.status_code == 200, "Formal staff login failed"
            staff_headers = {
                "Authorization": "Bearer " + credential.json()["access_token"]
            }
            removed_bypass = await client.post(
                "/_test/credentials",
                headers={"X-Internal-API-Key": SERVICE_KEY},
            )
            assert removed_bypass.status_code == 404
            path = "/v1/workflow-webhooks/" + str(receipt.webhook.id)
            from app.services.webhook_http import SIGNATURE_HEADERS

            async def send(event):
                body, headers = signed(receipt, event=event)
                wire = {
                    SIGNATURE_HEADERS[name]: value
                    for name, value in headers.items()
                }
                wire["Content-Type"] = "application/json"
                return await client.post(path, content=body, headers=wire)

            first = await send("live-001")
            assert first.status_code == 202
            identifier = first.json()["delivery_id"]
            async with fixture.sessions() as db:
                assert (
                    await db.scalar(
                        select(func.count()).select_from(WorkflowExecution)
                    )
                    == 0
                )
            # The HTTP handler deliberately lost its queue acknowledgement.
            # Only the real production Beat schedule may recover the delivery.
            beat = spawn("beat")
            await completed(identifier)
            assert beat.poll() is None, "Owned Beat process exited"

            public_root = f"/v1/ai/workflows/{fixture.workflow_id}/webhook"
            anonymous = await client.get(public_root)
            assert anonymous.status_code in {401, 403}
            staff_state = await client.get(public_root, headers=staff_headers)
            assert staff_state.status_code == 200
            assert staff_state.headers["Cache-Control"] == "no-store"
            assert (
                staff_state.json()["current_release"]["release_id"]
                == fixture.release_id
            )
            staff_history = await client.get(
                public_root + "/deliveries", headers=staff_headers
            )
            assert staff_history.status_code == 200
            assert staff_history.headers["Cache-Control"] == "no-store"
            assert staff_history.json()["items"][0]["id"] == identifier
            assert "synthetic-private-notification" not in staff_history.text
            assert "payload_ciphertext" not in staff_history.text
            foreign = await client.get(
                f"/v1/ai/workflows/{uuid4()}/webhook",
                headers=staff_headers,
            )
            assert foreign.status_code == 404
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{workflow_port}",
                trust_env=False,
                timeout=15,
            ) as management:
                root = f"/v1/workflows/{fixture.workflow_id}/webhook"
                params = {"project_id": str(fixture.project_id)}
                headers = {"X-Internal-API-Key": SERVICE_KEY}
                state = await management.get(
                    root,
                    params=params,
                    headers=headers,
                )
                assert state.status_code == 200
                assert (
                    state.json()["current_release"]["release_id"]
                    == fixture.release_id
                )
                history = await management.get(
                    root + "/deliveries",
                    params=params,
                    headers=headers,
                )
                assert history.status_code == 200
                assert history.headers["Cache-Control"] == "no-store"
                record = history.json()["items"][0]
                assert history.json()["total"] == 1
                assert record["id"] == identifier
                assert record["execution_status"] == "completed"
                assert "synthetic-private-notification" not in history.text
                assert "payload_ciphertext" not in history.text
            duplicate = await send("live-001")
            assert (
                duplicate.status_code == 202
                and duplicate.json()["deduplicated"]
            )
            worker.terminate()
            await asyncio.to_thread(worker.wait, timeout=15)
            second = await send("live-002")
            assert (
                second.status_code == 202
                and second.json()["status"] == "pending"
            )
            spawn("worker")
            await completed(second.json()["delivery_id"])
            assert beat.poll() is None, "Owned Beat process exited"
            from tests.integration.webhook_formal_management import (
                exercise_formal_configuration,
            )

            await exercise_formal_configuration(
                client, staff_headers, completed, signed, extra_hooks,
            )
            revoked = await client.post(
                "/_test/revoke", headers={"X-Internal-API-Key": SERVICE_KEY}
            )
            assert revoked.status_code == 200
            refused = await send("live-003")
            assert refused.status_code == 403
            stale_history = await client.get(
                public_root + "/deliveries", headers=staff_headers
            )
            assert stale_history.status_code == 401
        async with fixture.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(WorkflowExecution)
                )
                == 3
            )
            assert (
                await db.scalar(
                    select(func.count()).select_from(NodeExecution)
                )
                == 6
            )
            assert (
                await db.scalar(
                    select(func.count()).select_from(WorkflowWebhookDelivery)
                )
                == 3
            )
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
        for log in logs:
            log.close()
        keys = [key async for key in redis.scan_iter(match=prefix + "*")]
        assert all(key.startswith(prefix.encode()) for key in keys)
        if keys:
            await redis.delete(*keys)
        edge = hashlib.sha256((prefix + "127.0.0.1").encode()).hexdigest()
        await redis.delete(
            "workflow:webhook-edge:v1:" + edge,
            "workflow:webhook-rate:v1:" + str(receipt.webhook.id),
        )
        for hook_id in extra_hooks:
            await redis.delete("workflow:webhook-rate:v1:" + hook_id)
        assert not [key async for key in redis.scan_iter(match=prefix + "*")]
        await redis.aclose()

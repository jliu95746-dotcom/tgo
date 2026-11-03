"""Exercise real source policies and natural Beat checks in owned infrastructure.

Only the waiting checkpoint is synthetic: Event/approval publication stays closed.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sys
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
from pydantic import SecretStr
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
logging.disable(logging.CRITICAL)
from app.config import settings  # noqa: E402
from app.models.approval import WorkflowApprovalRequest  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402
from app.services.event_source_audit import (  # noqa: E402
    RedisAuditProgress,
    audit_namespace,
)
from app.services.timer_probe import probe_key, probe_scope  # noqa: E402

logging.disable(logging.CRITICAL)


def helpers():
    spec = importlib.util.spec_from_file_location(
        "event_audit_helpers", Path(__file__).with_name("workflow-timers-live.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def dispatch_child():
    helpers().ensure_local()
    database = os.environ["TGO_EVENT_AUDIT_DATABASE"]
    assert make_url(settings.DATABASE_URL).database == database
    assert re.fullmatch(r"tgo_scenarios_test_event_audit_[a-f0-9]{32}", database)
    from celery_app.event_source_tasks import audit_waiting_event_sources_task

    audit_waiting_event_sources_task.apply_async(
        task_id=os.environ["TGO_EVENT_AUDIT_TASK"], queue=settings.WORKFLOW_QUEUE_NAME
    )


async def main():
    helper = helpers()
    helper.ensure_local()
    nonce = uuid4().hex
    database, queue = "tgo_scenarios_test_event_audit_" + nonce, "event-audit-" + nonce
    directory = ROOT / ".tmp/native-dev" / queue
    directory.mkdir(parents=True, exist_ok=False)
    processes = helper.OwnedProcesses(directory)
    url = make_url(settings.DATABASE_URL)
    target = url.set(database=database)
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    engine = create_async_engine(target, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    broker = Redis.from_url(settings.REDIS_URL)
    private, absent_key = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    project = str(uuid4())
    port, private_port = helper.free_port(), helper.free_port()
    base, private_base = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{private_port}"
    environment = {
        **os.environ,
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "SECRET_KEY": private,
        "WORKFLOW_QUEUE_NAME": queue,
        "API_INTERNAL_SERVICE_URL": private_base,
        "WORKFLOW_EVENT_INPUT_KEYS": "",
        "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID": "",
        "WORKFLOW_EVENT_RECEIPT_KEYS": "",
        "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID": "",
        "WORKFLOW_APPROVAL_KEYS": "",
        "WORKFLOW_APPROVAL_ACTIVE_KEY_ID": "",
        "TIMER_EXECUTION_ENABLED": "false",
        "WEBHOOK_EXECUTION_ENABLED": "false",
        "DATABASE_ECHO": "false",
        "SAAS_ENABLED": "false",
        "PYTHONUTF8": "1",
        "TGO_EVENT_AUDIT_DATABASE": database,
        "TGO_EVENT_AUDIT_PROJECT": project,
        "TGO_EVENT_AUDIT_BINDINGS": str(directory / "source-bindings.json"),
    }
    with patch.multiple(
        settings,
        DATABASE_URL=environment["DATABASE_URL"],
        WORKFLOW_QUEUE_NAME=queue,
        API_INTERNAL_SERVICE_URL=private_base,
        SECRET_KEY=SecretStr(private),
    ):
        namespace, scope = audit_namespace(), probe_scope()
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
        "database": database,
        "run_directory": str(directory),
        "synthetic_wait_only": True,
        "owned_database_removed": False,
        "owned_processes_stopped": False,
        "owned_queue_removed": False,
    }
    created, runs, cases = False, [], []
    task_id = str(uuid4())
    environment["TGO_EVENT_AUDIT_TASK"] = task_id

    def stage(value):
        report["stage"] = value
        print(json.dumps({"stage": value}), flush=True)

    def start_api(name, private_api=False):
        return processes.start(
            name,
            ROOT / "repos/tgo-api/.venv/Scripts/python.exe"
            if private_api
            else sys.executable,
            [
                "-m",
                "uvicorn",
                "app.internal:internal_app" if private_api else "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(private_port if private_api else port),
                "--no-access-log",
            ],
            ROOT / "repos/tgo-api" if private_api else SERVICE,
            environment,
        )

    def start_worker(name):
        return processes.start(
            name,
            sys.executable,
            [
                "-m",
                "celery",
                "-A",
                "celery_app.celery",
                "worker",
                "--pool=solo",
                "--loglevel=info",
                "--without-gossip",
                "--without-mingle",
                "--hostname=" + name + "@" + queue,
                "-Q",
                queue,
            ],
            SERVICE,
            environment,
        )

    async def ready(client, process, endpoint):
        for _ in range(240):
            assert process.poll() is None, "Owned API stopped"
            try:
                if (await client.get(endpoint + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned API readiness observation expired")

    async def authority_fixture(action):
        process = processes.start(
            "authority-" + action,
            ROOT / "repos/tgo-api/.venv/Scripts/python.exe",
            [
                str(
                    Path(__file__).with_name(
                        "workflow_event_audit_authority_fixture.py"
                    )
                ),
                action,
            ],
            ROOT / "repos/tgo-api",
            environment,
        )
        assert (
            await asyncio.to_thread(process.wait, timeout=60) == 0
        ), "Owned authority fixture failed"

    async def assert_waits():
        async with sessions() as db:
            for identifier, approval_id, frozen in runs:
                run = await db.get(WorkflowExecution, identifier)
                request = await db.get(WorkflowApprovalRequest, approval_id)
                assert run.status == "waiting_approval" and run.input == frozen
                assert (
                    request.status == "pending"
                    and request.checkpoint["capture_status"] == "encrypted"
                )
            assert not (await db.scalars(select(NodeExecution))).all()

    async def wait_audit(worker):
        for _ in range(200):
            assert worker.poll() is None
            if broker.get(namespace + ":cursor") == max(
                row[0] for row in runs
            ).encode() and not broker.exists(namespace + ":lease"):
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Natural Beat source audit observation expired")

    async def wait_owned_idle(worker):
        # Stop Beat first. Queued or prefetched maintenance work must finish
        # before terminating the Worker; an empty list alone is insufficient.
        for _ in range(160):
            assert worker.poll() is None
            own_unacked = any(
                json.loads(value)[2] == queue for value in broker.hvals("unacked")
            )
            if not broker.llen(queue) and not own_unacked:
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("Owned maintenance tasks did not become idle")

    async def dispatch_check(worker, label):
        identifier = str(uuid4())
        environment["TGO_EVENT_AUDIT_TASK"] = identifier
        dispatch = processes.start(
            label,
            sys.executable,
            [str(Path(__file__).resolve()), "--child-dispatch"],
            SERVICE,
            environment,
        )
        assert await asyncio.to_thread(dispatch.wait, timeout=30) == 0
        for _ in range(160):
            result = broker.get("celery-task-meta-" + identifier)
            if result and json.loads(result).get("status") == "SUCCESS":
                assert json.loads(result)["result"] == 0
                return
            assert worker.poll() is None
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned repeat audit did not complete")

    try:
        stage("create_owned_database")
        assert broker.ping()
        async with admin.connect() as db:
            assert not await db.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": database},
            )
            await db.execute(text('CREATE DATABASE "' + database + '"'))
            created = True
        config = Config(str(SERVICE / "alembic.ini"))
        config.set_main_option("script_location", str(SERVICE / "migrations"))
        async with engine.connect() as db:

            def migrate(connection):
                config.attributes["connection"] = connection
                command.upgrade(config, "head")

            await db.run_sync(migrate)
            await db.commit()
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            api = start_api("workflow-api")
            await ready(client, api, base)

            async def request(method, path, expected=200, **kwargs):
                response = await client.request(method, base + path, **kwargs)
                assert (
                    response.status_code == expected
                ), f"Workflow HTTP {response.status_code}: {path}"
                return response.json()

            stage("publish_source_bound_flows")
            for case in ("disabled", "rebound", "cleared", "staff_revoked"):
                graph = {
                    "name": "合成来源巡检-" + case,
                    "nodes": [
                        {
                            "id": "event",
                            "type": "event",
                            "position": {"x": 0, "y": 0},
                            "data": {
                                "type": "event",
                                "reference_key": "event",
                                "label": "会话结束",
                                "event_type": "staff.session.closed",
                            },
                        },
                        {
                            "id": "answer",
                            "type": "answer",
                            "position": {"x": 300, "y": 0},
                            "data": {
                                "type": "answer",
                                "reference_key": "answer",
                                "label": "合成结果",
                                "output_type": "variable",
                                "output_variable": "event.data",
                            },
                        },
                    ],
                    "edges": [
                        {"id": "event-answer", "source": "event", "target": "answer"}
                    ],
                }
                flow = await request(
                    "POST",
                    "/v1/workflows/",
                    201,
                    params={"project_id": project},
                    json=graph,
                )
                path = "/v1/workflows/" + flow["id"]
                await request(
                    "POST",
                    path + "/publish",
                    params={"project_id": project},
                    json={
                        "expected_draft_version": flow["version"],
                        "expected_publication_revision": flow["publication_revision"],
                    },
                )
                context = await request(
                    "GET",
                    path + "/event-test-context",
                    params={"project_id": project},
                    headers={"X-Internal-API-Key": private},
                )
                cases.append(
                    {
                        "case": case,
                        "workflow_id": flow["id"],
                        "release_id": context["release_id"],
                        "release_digest": context["release_digest"],
                    }
                )
            environment["TGO_EVENT_AUDIT_CASES"] = json.dumps(cases)
            stage("seed_actual_private_api_authority")
            await authority_fixture("seed")
            cases = json.loads(
                (directory / "source-bindings.json").read_text(encoding="utf-8")
            )
            authority = start_api("authority-api", True)
            await ready(client, authority, private_base)

            async def source_status(row, expected):
                query = {
                    "project_id": project,
                    "workflow_id": row["workflow_id"],
                    "event_type": "staff.session.closed",
                    "release_id": row["release_id"],
                    "release_digest": row["release_digest"],
                    "source_binding": row["source_binding"],
                }
                response = await client.post(
                    private_base + "/internal/workflows/events/source/verify",
                    json=query,
                    headers={"X-Internal-API-Key": private},
                )
                assert (
                    response.status_code == expected
                ), f"Actual authority HTTP {response.status_code} for {row['case']}"

            stage("accept_real_authorized_events_and_stage_synthetic_waits")
            for row in cases:
                await source_status(row, 200)
                body = json.dumps(
                    {
                        "project_id": project,
                        "event_type": "staff.session.closed",
                        "data": {"text": "synthetic-private-body"},
                        "expected_release_id": row["release_id"],
                        "expected_release_digest": row["release_digest"],
                        "source_binding": row["source_binding"],
                    },
                    separators=(",", ":"),
                ).encode()
                event_id, instant = "source-" + row["source_binding"][
                    "delivery_id"
                ], str(int(datetime.now(timezone.utc).timestamp()))
                message = b"\n".join(
                    [
                        instant.encode(),
                        event_id.encode(),
                        row["workflow_id"].encode(),
                        body,
                    ]
                )
                signature = hmac.new(
                    private.encode(), message, hashlib.sha256
                ).hexdigest()
                accepted = await request(
                    "POST",
                    "/v1/workflows/" + row["workflow_id"] + "/events",
                    202,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Workflow-Timestamp": instant,
                        "X-Workflow-Event-ID": event_id,
                        "X-Workflow-Signature": "sha256=" + signature,
                    },
                )
                async with sessions() as db:
                    run = await db.get(WorkflowExecution, accepted["execution_id"])
                    assert (
                        run.status == "pending"
                        and "synthetic-private-body" not in json.dumps(run.input)
                    )
                    approval = WorkflowApprovalRequest(
                        project_id=project,
                        execution_id=run.id,
                        node_id="synthetic-approval",
                        approver_staff_id=row["actor_id"],
                        summary="合成等待生命周期验收",
                        checkpoint={
                            "capture_status": "encrypted",
                            "key_id": "absent",
                            "ciphertext": "synthetic-unreadable-cursor",
                        },
                        status="pending",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    )
                    db.add(approval)
                    await db.flush()
                    run.status, run.active_approval_id = "waiting_approval", approval.id
                    runs.append((run.id, approval.id, dict(run.input)))
                    await db.commit()
            report["checks"].append(
                "four_actual_private_source_grants_and_encrypted_http_inputs"
            )
            queued = [json.loads(value) for value in broker.lrange(queue, 0, -1)]
            assert len(queued) == 4
            assert {item["headers"]["id"] for item in queued} == {
                row[0] for row in runs
            }
            assert all(
                item["headers"]["task"] == "execute_workflow_task" for item in queued
            )
            # No Worker exists yet; drain only the four verified owned dispatches.
            assert len(broker.lrange(queue, 0, -1)) == 4
            broker.delete(queue)

            stage("verify_real_redis_lease_ownership")
            async with AsyncRedis.from_url(settings.REDIS_URL) as redis:
                progress = RedisAuditProgress(redis, namespace)
                assert await progress.acquire("first") and not await progress.acquire(
                    "other"
                )
                assert not await progress.advance("other", runs[0][0])
                await progress.release("other")
                assert await redis.get(namespace + ":lease") == b"first"
                await redis.expire(namespace + ":lease", 1)
                for _ in range(40):
                    if not await redis.exists(namespace + ":lease"):
                        break
                    await asyncio.sleep(0.05)
                assert await progress.acquire("second")
                assert not await progress.advance("first", runs[0][0])
                await progress.release("first")
                assert await redis.get(namespace + ":lease") == b"second"
                assert await progress.advance("second", "")
                await progress.release("second")
            report["checks"].append(
                "expired_redis_owner_cannot_advance_or_delete_successor_lease"
            )
            environment.update(
                {
                    "WORKFLOW_EVENT_INPUT_KEYS": json.dumps({"new": absent_key}),
                    "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID": "new",
                    "WORKFLOW_APPROVAL_KEYS": json.dumps({"new": absent_key}),
                    "WORKFLOW_APPROVAL_ACTIVE_KEY_ID": "new",
                }
            )
            worker = start_worker("worker-before-restart")
            beat = processes.start(
                "beat",
                sys.executable,
                [
                    "-m",
                    "celery",
                    "-A",
                    "celery_app.celery",
                    "beat",
                    "--loglevel=info",
                    "--schedule",
                    str(directory / "beat.db"),
                ],
                SERVICE,
                environment,
            )
            stage("wait_for_natural_beat_audit_with_missing_payload_keys")
            await wait_audit(worker)
            await assert_waits()
            assert beat.poll() is None
            report["checks"].append(
                "natural_beat_preserves_authorized_waits_without_body_or_cursor_keys"
            )
            processes.stop(beat)
            await wait_owned_idle(worker)
            stage("repeat_source_audit_in_the_same_worker_process")
            worker_log = directory / "worker-before-restart.log"
            before = worker_log.read_text(encoding="utf-8", errors="replace").count(
                "200 OK"
            )
            await dispatch_check(worker, "dispatch-repeat-check")
            await assert_waits()
            assert (
                worker_log.read_text(encoding="utf-8", errors="replace").count("200 OK")
                == before + 4
            )
            report["checks"].append(
                "same_worker_repeated_audit_uses_fresh_http_pool_and_four_real_grants"
            )

            stage("restart_owned_worker_and_defer_authority_outage")
            await wait_owned_idle(worker)
            processes.stop(worker)
            processes.stop(authority)
            worker = start_worker("worker-after-restart")
            await dispatch_check(worker, "dispatch-outage-check")
            await assert_waits()
            assert (
                broker.get(namespace + ":cursor")
                == max(row[0] for row in runs).encode()
            )
            report["checks"].append(
                "real_authority_outage_and_worker_restart_preserve_wait_and_durable_cursor"
            )
            authority = start_api("authority-api-restored", True)
            await ready(client, authority, private_base)
            await authority_fixture("mutate")
            for row in cases:
                await source_status(row, 409)
            # Reset only our cursor; natural Beat must advance it again.
            broker.delete(namespace + ":cursor")
            beat = processes.start(
                "beat-after-restart",
                sys.executable,
                [
                    "-m",
                    "celery",
                    "-A",
                    "celery_app.celery",
                    "beat",
                    "--loglevel=info",
                    "--schedule",
                    str(directory / "beat-restored.db"),
                ],
                SERVICE,
                environment,
            )
            stage("wait_for_natural_beat_revocation_audit")
            await wait_audit(worker)
            async with sessions() as db:
                for identifier, approval_id, _ in runs:
                    run = await db.get(WorkflowExecution, identifier)
                    approval = await db.get(WorkflowApprovalRequest, approval_id)
                    assert (
                        run.status == "cancelled"
                        and run.input is None
                        and run.output is None
                    )
                    assert (
                        approval.status == "cancelled"
                        and approval.decided_at is not None
                        and approval.decided_by is None
                    )
                    assert approval.checkpoint["capture_status"] == "redacted"
                assert not (await db.scalars(select(NodeExecution))).all()
            assert beat.poll() is None
            report["checks"].append(
                "natural_beat_cancels_disabled_rebound_cleared_and_revoked_staff_sources"
            )
            report["checks"].append(
                "pending_approvals_cancelled_and_payloads_redacted_without_node_execution"
            )
            processes.stop(beat)
            await wait_owned_idle(worker)
            processes.stop(worker)
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise RuntimeError(
            "Owned event source audit failed at "
            + report["stage"]
            + ": "
            + type(error).__name__
        ) from None
    finally:
        errors = []
        try:
            processes.close()
            report["owned_processes_stopped"] = True
        except Exception:
            errors.append("owned_process_cleanup_failed")
        await engine.dispose()
        if created and report["owned_processes_stopped"]:
            try:
                async with admin.connect() as db:
                    await db.execute(
                        text('DROP DATABASE "' + database + '" WITH (FORCE)')
                    )
                    report["owned_database_removed"] = not bool(
                        await db.scalar(
                            text("SELECT 1 FROM pg_database WHERE datname=:name"),
                            {"name": database},
                        )
                    )
            except Exception:
                errors.append("owned_database_cleanup_failed")
        try:
            keys = [
                queue,
                "_kombu.binding." + queue,
                namespace + ":lease",
                namespace + ":cursor",
                "celery-task-meta-" + task_id,
            ]
            keys += ["celery-task-meta-" + row[0] for row in runs]
            keys += [probe_key(scope, kind) for kind in ("beat", "challenge", "worker")]
            for log in directory.glob("worker-*.log"):
                keys += [
                    "celery-task-meta-" + identifier
                    for identifier in re.findall(
                        r"Task [^\s\[]+\[([^\]]+)\]",
                        log.read_text(encoding="utf-8", errors="replace"),
                    )
                ]
            broker.delete(*keys)
            report["owned_unacked_remaining"] = sum(
                json.loads(value)[2] == queue for value in broker.hvals("unacked")
            )
            report["owned_queue_removed"] = (
                not bool(
                    broker.exists(queue, namespace + ":lease", namespace + ":cursor")
                )
                and report["owned_unacked_remaining"] == 0
            )
            broker.close()
        except Exception:
            errors.append("owned_queue_cleanup_failed")
        await admin.dispose()
        bindings_file = directory / "source-bindings.json"
        if bindings_file.exists():
            bindings_file.unlink()
        report["logs_contain_test_secrets"] = any(
            secret and secret in log.read_text(encoding="utf-8", errors="replace")
            for log in directory.glob("*.log")
            for secret in (private, absent_key, url.password or "")
        )
        report["cleanup_errors"] = errors
        report["verification_completed"] = bool(
            report["verification_completed"]
            and not errors
            and report["owned_database_removed"]
            and report["owned_queue_removed"]
            and report["owned_processes_stopped"]
            and not report["logs_contain_test_secrets"]
        )
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        (ROOT / ".tmp/native-dev/workflow-event-source-audit-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child-dispatch", action="store_true")
    args = parser.parse_args()
    if args.child_dispatch:
        asyncio.run(dispatch_child())
    elif args.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

"""Rotate input keys through actual event HTTP, Redis and Workers."""

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
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
from redis import Redis
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402

logging.disable(logging.CRITICAL)


def helpers():
    spec = importlib.util.spec_from_file_location(
        "event_input_runtime_helpers",
        Path(__file__).with_name("workflow-timers-live.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def recover_child():
    assert __debug__
    target = os.environ["TGO_EVENT_INPUT_TEST_DATABASE"]
    assert re.fullmatch(r"tgo_scenarios_test_event_input_[a-f0-9]{32}", target)
    for value in (settings.DATABASE_URL, settings.REDIS_URL):
        assert make_url(value).host in {"localhost", "127.0.0.1", "::1"}
    assert make_url(settings.DATABASE_URL).database == target
    from celery_app.tasks import sweep_pending_initial

    assert await sweep_pending_initial() == 1


async def main():
    helper = helpers()
    helper.ensure_local()
    identity = uuid4().hex
    database, queue = (
        "tgo_scenarios_test_event_input_" + identity,
        "event-input-" + identity,
    )
    directory = ROOT / ".tmp/native-dev" / queue
    directory.mkdir(parents=True, exist_ok=False)
    processes = helper.OwnedProcesses(directory)
    url = make_url(settings.DATABASE_URL)
    target = url.set(database=database)
    admin = create_async_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    engine = create_async_engine(target, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    broker = Redis.from_url(settings.REDIS_URL)
    private_key, old_key, new_key = (
        secrets.token_urlsafe(48) for _ in range(3)
    )
    project, event_id = str(uuid4()), "synthetic-event-" + uuid4().hex
    port = helper.free_port()
    base = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": target.render_as_string(hide_password=False),
            "TGO_EVENT_INPUT_TEST_DATABASE": database,
            "SECRET_KEY": private_key,
            "WORKFLOW_QUEUE_NAME": queue,
            "WORKFLOW_EVENT_RECEIPT_KEYS": "",
            "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID": "",
            "WORKFLOW_APPROVAL_KEYS": "",
            "WORKFLOW_APPROVAL_ACTIVE_KEY_ID": "",
            "WORKFLOW_EVENT_INPUT_KEYS": json.dumps({"old": old_key}),
            "WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID": "old",
            "TIMER_EXECUTION_ENABLED": "false",
            "WEBHOOK_EXECUTION_ENABLED": "false",
            "DATABASE_ECHO": "false",
            "PYTHONUTF8": "1",
        }
    )
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
        "owned_database_removed": False,
        "owned_processes_stopped": False,
        "owned_queue_removed": False,
        "run_directory": str(directory),
        "database": database,
    }
    created, run_id = False, None

    async def ready(client, api):
        for _ in range(240):
            assert api.poll() is None
            try:
                if (await client.get(base + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned event API readiness observation expired")

    def start_api(name):
        return processes.start(
            name,
            sys.executable,
            [
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--no-access-log",
            ],
            SERVICE,
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

    async def wait_run(worker, predicate):
        for _ in range(240):
            assert worker.poll() is None
            async with sessions() as db:
                run = await db.get(WorkflowExecution, run_id)
                if run is not None and predicate(run):
                    return run
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned event run state observation expired")

    try:
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
            api = start_api("api-old-key")
            await ready(client, api)

            async def request(method, path, expected=200, **kwargs):
                response = await client.request(method, base + path, **kwargs)
                assert response.status_code == expected, (
                    f"Workflow HTTP {response.status_code}: {path}"
                )
                return response.json()

            graph = {
                "name": "独立事件正文密钥验收",
                "nodes": [
                    {
                        "id": "event",
                        "type": "event",
                        "position": {"x": 0, "y": 0},
                        "data": {
                            "type": "event",
                            "reference_key": "event",
                            "label": "合成事件",
                            "event_type": "qa.received",
                        },
                    },
                    {
                        "id": "answer",
                        "type": "answer",
                        "position": {"x": 300, "y": 0},
                        "data": {
                            "type": "answer",
                            "reference_key": "answer",
                            "label": "原正文返回",
                            "output_type": "variable",
                            "output_variable": "event.data",
                        },
                    },
                ],
                "edges": [
                    {
                        "id": "event-answer",
                        "source": "event",
                        "target": "answer",
                    }
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
                    "expected_publication_revision": flow[
                        "publication_revision"
                    ],
                },
            )
            expected_data = {
                "excerpt": "synthetic-private-event-body",
                "nested": {"quantity": 2},
            }
            body = json.dumps(
                {
                    "project_id": project,
                    "event_type": "qa.received",
                    "data": expected_data,
                },
                separators=(",", ":"),
            ).encode()

            async def send_event():
                instant = str(int(datetime.now(timezone.utc).timestamp()))
                message = (
                    instant.encode()
                    + b"\n"
                    + event_id.encode()
                    + b"\n"
                    + flow["id"].encode()
                    + b"\n"
                    + body
                )
                signature = hmac.new(
                    private_key.encode(), message, hashlib.sha256
                ).hexdigest()
                return await request(
                    "POST",
                    path + "/events",
                    202,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Workflow-Timestamp": instant,
                        "X-Workflow-Event-ID": event_id,
                        "X-Workflow-Signature": "sha256=" + signature,
                    },
                )

            accepted = await send_event()
            run_id = accepted["execution_id"]
            async with sessions() as db:
                run = await db.get(WorkflowExecution, run_id)
                frozen = json.dumps(run.input, sort_keys=True)
                assert expected_data["excerpt"] not in frozen
                assert run.input["_event_payload"]["key_id"] == "old"
                assert run.status == "pending"
            report["checks"].append("api_persists_only_encrypted_body")
            processes.stop(api)
            environment["WORKFLOW_EVENT_INPUT_KEYS"] = json.dumps(
                {"new": new_key}
            )
            environment["WORKFLOW_EVENT_INPUT_ACTIVE_KEY_ID"] = "new"
            api = start_api("api-new-only")
            await ready(client, api)
            repeated = await send_event()
            assert (
                repeated["deduplicated"] and repeated["execution_id"] == run_id
            )
            missing = start_worker("worker-missing-old")
            run = await wait_run(
                missing,
                lambda row: row.status == "pending" and row.error is not None,
            )
            assert "密钥" in run.error
            assert json.dumps(run.input, sort_keys=True) == frozen
            async with sessions() as db:
                assert (await db.scalars(select(NodeExecution))).all() == []
            # Wait for the consumed message's result before stopping its owner.
            for _ in range(120):
                result = broker.get("celery-task-meta-" + run_id)
                if result and json.loads(result).get("status") == "SUCCESS":
                    break
                assert missing.poll() is None
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Key-wait delivery was not acknowledged")
            processes.stop(missing)
            report["checks"].append(
                "missing_key_retains_unclaimed_original_and_dedup"
            )
            environment["WORKFLOW_EVENT_INPUT_KEYS"] = json.dumps(
                {"old": old_key, "new": new_key}
            )
            recovered = start_worker("worker-restored-old")
            async with sessions() as db:
                run = await db.get(WorkflowExecution, run_id)
                # Only this synthetic wait is aged; no system clock changes.
                run.started_at = datetime.now(timezone.utc) - timedelta(
                    minutes=3
                )
                await db.commit()
            child = processes.start(
                "recover-pending",
                sys.executable,
                [str(Path(__file__)), "--child-recover"],
                ROOT,
                environment,
            )
            assert await asyncio.to_thread(child.wait, 90) == 0
            run = await wait_run(
                recovered, lambda row: row.status == "completed"
            )
            assert run.input is None and run.error is None
            assert run.output == {"result": expected_data}
            async with sessions() as db:
                assert (
                    await db.scalars(select(NodeExecution.node_id))
                ).all() == ["event", "answer"]
            again = await send_event()
            assert again["execution_id"] == run_id and again["deduplicated"]
            report["checks"].append(
                "restored_key_actual_recovery_single_run_"
                "original_body_and_cleanup"
            )
            for _ in range(120):
                result = broker.get("celery-task-meta-" + run_id)
                if result and json.loads(result).get("status") == "SUCCESS":
                    break
                assert recovered.poll() is None
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Recovered delivery was not acknowledged")
            processes.stop(recovered)
            # A new event accepted by the new-only API uses the new active key.
            event_id = "synthetic-new-event-" + uuid4().hex
            fresh = await send_event()
            second_id = fresh["execution_id"]
            async with sessions() as db:
                pending = await db.get(WorkflowExecution, second_id)
                assert pending.status == "pending"
                assert pending.input["_event_payload"]["key_id"] == "new"
            environment["WORKFLOW_EVENT_INPUT_KEYS"] = json.dumps(
                {"new": new_key}
            )
            new_only = start_worker("worker-new-only")
            for _ in range(240):
                async with sessions() as db:
                    second = await db.get(WorkflowExecution, second_id)
                    if second.status == "completed":
                        assert second.input is None and second.output == {
                            "result": expected_data
                        }
                        break
                assert new_only.poll() is None
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("New active-key event did not finish")
            report["checks"].append(
                "new_api_and_new_only_worker_use_active_key"
            )
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise
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
                            text(
                                "SELECT 1 FROM pg_database WHERE datname=:name"
                            ),
                            {"name": database},
                        )
                    )
            except Exception:
                errors.append("owned_database_cleanup_failed")
        try:
            keys = [queue, "_kombu.binding." + queue]
            for log in directory.glob("worker-*.log"):
                for task in re.findall(
                    r"Task [^\s\[]+\[([^\]]+)\]",
                    log.read_text(encoding="utf-8", errors="replace"),
                ):
                    keys.append("celery-task-meta-" + task)
            if run_id:
                keys.append("celery-task-meta-" + run_id)
            broker.delete(*keys)
            report["owned_queue_removed"] = not bool(broker.exists(queue))
            broker.close()
        except Exception:
            errors.append("owned_queue_cleanup_failed")
        await admin.dispose()
        report["logs_contain_test_secrets"] = any(
            secret
            and secret in log.read_text(encoding="utf-8", errors="replace")
            for log in directory.glob("*.log")
            for secret in (private_key, old_key, new_key, url.password or "")
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
        (ROOT / ".tmp/native-dev/workflow-event-input-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child-recover", action="store_true")
    arguments = parser.parse_args()
    if arguments.child_recover:
        asyncio.run(recover_child())
    elif arguments.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

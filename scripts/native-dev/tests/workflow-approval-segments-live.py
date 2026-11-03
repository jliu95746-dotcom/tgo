"""Verify approval deadlines across real HTTP, Redis and restarted Workers."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
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
from app.models.approval import WorkflowApprovalRequest  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402
from app.models.workflow_schedule import WorkflowSchedule  # noqa: E402

logging.disable(logging.CRITICAL)


def runtime_helpers():
    spec = importlib.util.spec_from_file_location(
        "approval_segment_helpers",
        Path(__file__).with_name("workflow-timers-live.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_owned_child():
    assert __debug__
    database = os.environ["TGO_TIMER_TEST_DATABASE"]
    assert re.fullmatch(r"tgo_scenarios_test_timer_[a-f0-9]{32}", database)
    for value in (settings.DATABASE_URL, settings.REDIS_URL):
        assert make_url(value).host in {"localhost", "127.0.0.1", "::1"}
    assert make_url(settings.DATABASE_URL).database == database


async def child(action):
    ensure_owned_child()
    if action == "collect":
        from app.services import timer_dispatch
        from celery_app.task_database import workflow_task_session

        async with workflow_task_session() as db:
            fact = await timer_dispatch.materialize_due(
                db,
                os.environ["TGO_SEGMENT_TEST_PLAN"],
            )
            assert fact is not None
            assert await timer_dispatch.dispatch_occurrence(db, fact)
    elif action == "expire":
        from celery_app.tasks import sweep_expired_internal_events

        expected = int(os.environ.get("TGO_SEGMENT_EXPECT_EXPIRED", "0"))
        assert await sweep_expired_internal_events() == expected
    else:
        from celery_app.tasks import execute_workflow_task

        # A second actual broker message must not replay completed nodes.
        execute_workflow_task.apply_async(
            kwargs={
                "execution_id": os.environ["TGO_SEGMENT_TEST_RUN"],
                "workflow_id": os.environ["TGO_SEGMENT_TEST_FLOW"],
                "inputs": {},
                "project_id": os.environ["TGO_TIMER_TEST_PROJECT"],
            },
            task_id="segment-duplicate-" + uuid4().hex,
            queue=settings.WORKFLOW_QUEUE_NAME,
        )


async def main():
    helpers = runtime_helpers()
    helpers.ensure_local()
    identity = uuid4().hex
    database = "tgo_scenarios_test_timer_" + identity
    queue = "approval-segment-" + identity
    directory = ROOT / ".tmp/native-dev" / queue
    directory.mkdir(parents=True, exist_ok=False)
    processes = helpers.OwnedProcesses(directory)
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
    private_key = secrets.token_urlsafe(48)
    project, actor = str(uuid4()), str(uuid4())
    wf_port, api_port = helpers.free_port(), helpers.free_port()
    while api_port == wf_port:
        api_port = helpers.free_port()
    wf_url, api_url = (
        f"http://127.0.0.1:{wf_port}",
        f"http://127.0.0.1:{api_port}",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": target.render_as_string(hide_password=False),
            "TGO_TIMER_TEST_DATABASE": database,
            "TGO_TIMER_TEST_PROJECT": project,
            "TGO_TIMER_TEST_ACTOR": actor,
            "SECRET_KEY": private_key,
            "WORKFLOW_QUEUE_NAME": queue,
            "TIMER_EXECUTION_ENABLED": "true",
            "API_INTERNAL_SERVICE_URL": api_url,
            "WORKFLOW_APPROVAL_KEYS": "",
            "WORKFLOW_APPROVAL_ACTIVE_KEY_ID": "",
            "SAAS_ENABLED": "false",
            "SAAS_BILLING_ENABLED": "false",
            "DATABASE_ECHO": "false",
            "PYTHONUTF8": "1",
        }
    )
    api_python = ROOT / "repos/tgo-api/.venv/Scripts/python.exe"
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
        "owned_processes_stopped": False,
        "owned_database_removed": False,
        "owned_queue_removed": False,
        "run_directory": str(directory),
        "database": database,
    }
    created = False
    run_id = None

    async def short_child(name, python, arguments, cwd):
        process = processes.start(name, python, arguments, cwd, environment)
        # Observation timeout does not start a replacement process.
        code = await asyncio.to_thread(process.wait, 90)
        assert code == 0, "Owned child failed: " + name

    async def ready(client, address, process):
        for _ in range(240):
            assert process.poll() is None
            try:
                if (await client.get(address + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned HTTP readiness observation expired")

    def worker(name):
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

    async def wait_status(expected, active_worker):
        for _ in range(240):
            assert active_worker.poll() is None
            async with sessions() as db:
                rows = (await db.scalars(select(WorkflowExecution))).all()
                if len(rows) == 1 and rows[0].status == expected:
                    return rows[0]
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned run state observation expired: " + expected)

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
        await short_child(
            "seed",
            api_python,
            [
                str(
                    Path(__file__).with_name(
                        "workflow_timer_authority_fixture.py"
                    )
                ),
                "seed",
            ],
            ROOT,
        )
        api = processes.start(
            "private-api",
            api_python,
            [
                "-m",
                "uvicorn",
                "app.internal:internal_app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            ROOT / "repos/tgo-api",
            environment,
        )
        workflow = processes.start(
            "workflow-api",
            sys.executable,
            [
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(wf_port),
                "--no-access-log",
            ],
            SERVICE,
            environment,
        )
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            await ready(client, api_url, api)
            await ready(client, wf_url, workflow)

            async def request(method, path, expected=200, **kwargs):
                response = await client.request(
                    method, wf_url + path, **kwargs
                )
                if response.status_code != expected:
                    detail = response.json().get("detail")
                    if isinstance(detail, list):
                        print(
                            json.dumps(
                                {
                                    "http_status": response.status_code,
                                    "path": path,
                                    "validation": [
                                        {
                                            key: item.get(key)
                                            for key in ("loc", "type", "msg")
                                        }
                                        for item in detail
                                        if isinstance(item, dict)
                                    ],
                                }
                            ),
                            flush=True,
                        )
                assert response.status_code == expected, (
                    f"Workflow HTTP {response.status_code}: {path}"
                )
                return response.json()

            nodes = [
                {
                    "id": "clock",
                    "type": "timer",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "type": "timer",
                        "reference_key": "clock",
                        "label": "合成定时",
                        "cron_expression": "* * * * *",
                        "timezone": "UTC",
                    },
                }
            ]
            for index in (1, 2):
                nodes.append(
                    {
                        "id": f"approval{index}",
                        "type": "approval",
                        "position": {"x": index * 200, "y": 0},
                        "data": {
                            "type": "approval",
                            "label": "合成确认",
                            "reference_key": f"approval{index}",
                            "approver_staff_id": actor,
                            "summary": "确认合成批次 {{clock.timestamp}}",
                            "expires_in_minutes": 60,
                        },
                    }
                )
            nodes.append(
                {
                    "id": "answer",
                    "type": "answer",
                    "position": {"x": 600, "y": 0},
                    "data": {
                        "type": "answer",
                        "label": "合成结果",
                        "reference_key": "answer",
                        "output_type": "template",
                        "output_template": "原批次 {{clock.timestamp}}",
                    },
                }
            )
            graph = {
                "name": "审批恢复计时合成验收",
                "nodes": nodes,
                "edges": [
                    {
                        "id": f"edge{i}",
                        "source": nodes[i]["id"],
                        "target": nodes[i + 1]["id"],
                    }
                    for i in range(3)
                ],
            }
            flow = await request(
                "POST",
                "/v1/workflows/",
                201,
                params={"project_id": project},
                json=graph,
            )
            flow_id = flow["id"]
            path = "/v1/workflows/" + flow_id
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
            release = await request(
                "GET",
                path + "/releases/1/integration",
                params={"project_id": project},
            )
            # The schedule itself is a synthetic fixture; no Beat or UI claim.
            plan_id = str(uuid4())
            async with sessions() as db:
                db.add(
                    WorkflowSchedule(
                        id=plan_id,
                        project_id=project,
                        workflow_id=flow_id,
                        revision=1,
                        enabled=True,
                        release_id=release["release_id"],
                        release_digest=release["definition_digest"],
                        cron_expression="* * * * *",
                        timezone="UTC",
                        next_run_at=datetime.now(timezone.utc).replace(
                            second=0, microsecond=0
                        ),
                        created_by=actor,
                        updated_by=actor,
                    )
                )
                await db.commit()
            environment["TGO_SEGMENT_TEST_PLAN"] = plan_id
            await short_child(
                "collect",
                sys.executable,
                [str(Path(__file__)), "--child", "collect"],
                ROOT,
            )
            active = worker("worker-first")
            run = await wait_status("waiting_approval", active)
            run_id = run.id
            expected_output = "原批次 " + run.input["scheduled_at"]
            environment["TGO_SEGMENT_TEST_RUN"] = run_id
            environment["TGO_SEGMENT_TEST_FLOW"] = flow_id
            original = datetime.now(timezone.utc) - timedelta(days=2)
            params = {"project_id": project, "actor_staff_id": actor}
            headers = {"x-internal-api-key": private_key}
            for index in (1, 2):
                processes.stop(active)
                async with sessions() as db:
                    run = await db.get(WorkflowExecution, run_id)
                    run.started_at = original
                    run.active_segment_started_at = original
                    await db.commit()
                approvals = await request(
                    "GET",
                    "/v1/workflows/approvals",
                    params=params,
                    headers=headers,
                )
                approval = next(
                    row
                    for row in approvals
                    if row["node_id"] == f"approval{index}"
                )
                response = await request(
                    "POST",
                    "/v1/workflows/approvals/" + approval["id"] + "/decision",
                    params=params,
                    headers=headers,
                    json={
                        "decision": "approve",
                        "preview_digest": approval["preview_digest"],
                    },
                )
                assert response["dispatch_status"] == "queued"
                assert broker.llen(queue) >= 1
                await short_child(
                    "pending-expiry-" + str(index),
                    sys.executable,
                    [str(Path(__file__)), "--child", "expire"],
                    ROOT,
                )
                resumed_after = datetime.now(timezone.utc)
                active = worker("worker-resume-" + str(index))
                expected = "waiting_approval" if index == 1 else "completed"
                run = await wait_status(expected, active)
                assert run.started_at == original
                assert run.active_segment_started_at >= resumed_after
                await short_child(
                    "resumed-expiry-" + str(index),
                    sys.executable,
                    [str(Path(__file__)), "--child", "expire"],
                    ROOT,
                )
                segment_start = run.active_segment_started_at
                await short_child(
                    "duplicate-" + str(index),
                    sys.executable,
                    [str(Path(__file__)), "--child", "duplicate"],
                    ROOT,
                )
                marker = "Skipping unclaimable workflow run " + run_id
                for _ in range(120):
                    if marker in (
                        directory / ("worker-resume-" + str(index) + ".log")
                    ).read_text(encoding="utf-8", errors="replace"):
                        break
                    assert active.poll() is None
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError(
                        "Duplicate queue message was not consumed"
                    )
                async with sessions() as db:
                    run = await db.get(WorkflowExecution, run_id)
                    assert run.active_segment_started_at == segment_start
                    assert run.status == expected
                report["checks"].append(
                    "long_wait_resume_and_duplicate_" + str(index)
                )
            async with sessions() as db:
                run = await db.get(WorkflowExecution, run_id)
                assert run.status == "completed" and run.input is None
                assert run.output["result"] == expected_output
                assert (
                    await db.scalars(select(NodeExecution.node_id))
                ).all() == [
                    "clock",
                    "approval1",
                    "approval2",
                    "answer",
                ]
                approvals = (
                    await db.scalars(select(WorkflowApprovalRequest))
                ).all()
                assert len(approvals) == 2
                assert all(
                    row.checkpoint["capture_status"] == "redacted"
                    for row in approvals
                )
            report["checks"].append(
                "original_input_single_nodes_and_terminal_cleanup"
            )
            # Exercise the actual PostgreSQL sweep with fresh running segments,
            # then simulate a lost Worker for those separately owned fixtures.
            deadline_runs = []
            async with sessions() as db:
                for source in (
                    "internal_event",
                    "scheduled_timer",
                    "external_webhook",
                ):
                    identifier = str(uuid4())
                    deadline_runs.append(identifier)
                    db.add(
                        WorkflowExecution(
                            id=identifier,
                            project_id=project,
                            workflow_id=flow_id,
                            status="running",
                            source_channel=source,
                            execution_mode="production",
                            started_at=original,
                            active_segment_started_at=datetime.now(
                                timezone.utc
                            ),
                            input={"data": "owned synthetic timeout fixture"},
                        )
                    )
                await db.commit()
            await short_child(
                "fresh-running-expiry",
                sys.executable,
                [str(Path(__file__)), "--child", "expire"],
                ROOT,
            )
            async with sessions() as db:
                for identifier in deadline_runs:
                    row = await db.get(WorkflowExecution, identifier)
                    assert row.status == "running"
                    row.active_segment_started_at = original
                await db.commit()
            environment["TGO_SEGMENT_EXPECT_EXPIRED"] = "3"
            await short_child(
                "overdue-running-expiry",
                sys.executable,
                [str(Path(__file__)), "--child", "expire"],
                ROOT,
            )
            async with sessions() as db:
                for identifier in deadline_runs:
                    row = await db.get(WorkflowExecution, identifier)
                    assert row.status == "failed" and row.input is None
                    assert row.output is None and row.started_at == original
            report["checks"].append(
                "three_source_running_deadlines_in_postgres"
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
                    report["owned_database_removed"] = not await db.scalar(
                        text("SELECT 1 FROM pg_database WHERE datname=:name"),
                        {"name": database},
                    )
            except Exception:
                errors.append("owned_database_cleanup_failed")
        try:
            keys = [queue, "_kombu.binding." + queue]
            # Matching task IDs are harvested only from owned Worker logs.
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
            for secret in (private_key, url.password or "")
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
        (
            ROOT / ".tmp/native-dev/workflow-approval-segments-report.json"
        ).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child", choices=["collect", "expire", "duplicate"])
    arguments = parser.parse_args()
    if arguments.child:
        asyncio.run(child(arguments.child))
    elif arguments.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

"""Verify real timer HTTP, Redis and Worker paths using only owned fixtures."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
from uuid import uuid4
from unittest.mock import patch

from alembic import command
from alembic.config import Config
import httpx
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402
from app.models.workflow_schedule import WorkflowSchedule  # noqa: E402
from app.models.workflow_timer_occurrence import (  # noqa: E402
    WorkflowTimerOccurrence,
)
from app.services.timer_probe import probe_key, probe_scope  # noqa: E402


def ensure_local() -> None:
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    for target in (settings.DATABASE_URL, settings.REDIS_URL):
        if make_url(target).host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Only loopback infrastructure is permitted")


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class OwnedProcesses:
    def __init__(self, directory: Path):
        self.directory = directory
        self.processes: list[subprocess.Popen] = []
        self.files = []

    def start(self, name, python, args, cwd, environment):
        log = (self.directory / (name + ".log")).open("ab")
        self.files.append(log)
        process = subprocess.Popen(
            [str(python), *args],
            cwd=cwd,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.processes.append(process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if process.poll() is None:
            raise RuntimeError("An owned child process did not stop")

    def close(self):
        for process in reversed(self.processes):
            self.stop(process)
        for log in self.files:
            log.close()


async def child_mode(action: str) -> None:
    ensure_local()
    target = os.environ["TGO_TIMER_TEST_DATABASE"]
    if make_url(settings.DATABASE_URL).database != target or not re.fullmatch(
        r"tgo_scenarios_test_timer_[0-9a-f]{32}", target
    ):
        raise RuntimeError("Owned test database mismatch")
    if action == "collect":
        from celery_app.timer_tasks import sweep_workflow_timers

        await sweep_workflow_timers()
    elif action == "recover":
        from celery_app.tasks import sweep_pending_initial

        await sweep_pending_initial()
    else:
        from app.services import timer_dispatch
        from app.services.workflow_dispatch import WorkflowInitialDispatchError
        from celery_app.task_database import workflow_task_session

        original = timer_dispatch.enqueue_pending_initial

        async def lose_acknowledgement(run):
            await original(run)  # Actual Redis publish precedes injected loss.
            raise WorkflowInitialDispatchError("synthetic_ack_lost")

        timer_dispatch.enqueue_pending_initial = lose_acknowledgement
        async with workflow_task_session() as db:
            identifier = await timer_dispatch.materialize_due(
                db, os.environ["TGO_TIMER_TEST_PLAN"]
            )
        assert identifier is not None
        async with workflow_task_session() as db:
            await timer_dispatch.dispatch_occurrence(db, identifier)


async def main() -> None:
    ensure_local()
    identity = uuid4().hex
    database = "tgo_scenarios_test_timer_" + identity
    queue = "timer-live-" + identity
    project, actor = str(uuid4()), str(uuid4())
    private_key = secrets.token_urlsafe(48)
    directory = ROOT / ".tmp/native-dev" / queue
    directory.mkdir(parents=True, exist_ok=False)
    report_path = ROOT / ".tmp/native-dev/workflow-timers-report.json"
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "queue": queue,
        "verification_completed": False,
        "checks": [],
        "owned_database_removed": False,
        "owned_processes_stopped": False,
        "owned_queue_removed": False,
    }
    url = make_url(settings.DATABASE_URL)
    admin = create_async_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    engine = create_async_engine(
        url.set(database=database), hide_parameters=True
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    processes = OwnedProcesses(directory)
    redis = Redis.from_url(settings.REDIS_URL)
    task_ids: set[str] = set()
    created = False
    wf_port, api_port = free_port(), free_port()
    while wf_port == api_port:
        api_port = free_port()
    wf_url, api_url = (
        f"http://127.0.0.1:{wf_port}",
        f"http://127.0.0.1:{api_port}",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": url.set(database=database).render_as_string(
                hide_password=False
            ),
            "TGO_TIMER_TEST_DATABASE": database,
            "TGO_TIMER_TEST_PROJECT": project,
            "TGO_TIMER_TEST_ACTOR": actor,
            "SECRET_KEY": private_key,
            "WORKFLOW_QUEUE_NAME": queue,
            "TIMER_EXECUTION_ENABLED": "true",
            "API_INTERNAL_SERVICE_URL": api_url,
            "SAAS_ENABLED": "false",
            "SAAS_BILLING_ENABLED": "false",
            "DATABASE_ECHO": "false",
            "PYTHONUTF8": "1",
        }
    )
    with patch.multiple(
        settings, DATABASE_URL=environment["DATABASE_URL"],
        SECRET_KEY=SecretStr(private_key), WORKFLOW_QUEUE_NAME=queue,
        API_INTERNAL_SERVICE_URL=api_url,
    ):
        health_scope = probe_scope()
    assert health_scope is not None
    api_python = ROOT / "repos/tgo-api/.venv/Scripts/python.exe"
    api_fixture = Path(__file__).with_name(
        "workflow_timer_authority_fixture.py"
    )

    async def short_child(name, python, args, cwd):
        process = processes.start(name, python, args, cwd, environment)
        try:
            code = await asyncio.to_thread(process.wait, 40)
        except subprocess.TimeoutExpired:
            raise RuntimeError("A fixture process timed out") from None
        if code:
            raise RuntimeError("A fixture process failed: " + name)

    async def ready(client, address, process):
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(
                    "An owned HTTP server exited before readiness"
                )
            try:
                if (await client.get(address + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
        raise RuntimeError("Owned HTTP server readiness timed out")

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
                "--loglevel=warning",
                "--without-gossip",
                "--without-mingle",
                "--hostname=" + name + "@" + queue,
                "-Q",
                queue,
            ],
            SERVICE,
            environment,
        )

    async def snapshot(plan):
        async with sessions() as db:
            occurrence = await db.scalar(
                select(WorkflowTimerOccurrence)
                .where(WorkflowTimerOccurrence.schedule_id == plan)
                .order_by(WorkflowTimerOccurrence.scheduled_at.desc())
            )
            if occurrence is None:
                return None, None, 0
            run = (
                await db.get(WorkflowExecution, occurrence.execution_id)
                if (occurrence.execution_id)
                else None
            )
            count = await db.scalar(
                select(func.count())
                .select_from(NodeExecution)
                .where(NodeExecution.execution_id == occurrence.execution_id)
            )
            if run is not None:
                task_ids.add(run.id)
            return occurrence, run, count

    async def wait_run(plan, status, worker, seconds=130):
        for _ in range(seconds * 2):
            if worker.poll() is not None:
                raise RuntimeError(
                    "An owned Worker exited during verification"
                )
            state = await snapshot(plan)
            if state[1] is not None and state[1].status == status:
                return state
            await asyncio.sleep(0.5)
        raise RuntimeError("Timer run did not reach expected state: " + status)

    try:
        await asyncio.to_thread(redis.ping)
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
        await short_child(
            "authority-seed", api_python, [str(api_fixture), "seed"], ROOT
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
                "--log-level",
                "warning",
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
                "--log-level",
                "warning",
            ],
            SERVICE,
            environment,
        )
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            await ready(client, api_url, api)
            await ready(client, wf_url, workflow)
            headers = {"x-internal-api-key": private_key}

            async def request(method, path, expected=200, **kwargs):
                response = await client.request(
                    method, wf_url + path, **kwargs
                )
                if response.status_code != expected:
                    raise RuntimeError(
                        f"Workflow HTTP {response.status_code}: {path}"
                    )
                return response.json() if response.content else None

            async def runtime_state(path):
                state = await request(
                    "GET", path + "/schedule", headers=headers,
                    params={"project_id": project},
                )
                return state["runtime"]

            async def create_plan(name, expression="* * * * *"):
                graph = {
                    "name": name,
                    "nodes": [
                        {
                            "id": "clock",
                            "type": "timer",
                            "position": {"x": 0, "y": 0},
                            "data": {
                                "type": "timer",
                                "label": "合成定时",
                                "reference_key": "clock",
                                "cron_expression": expression,
                                "timezone": "UTC",
                            },
                        },
                        {
                            "id": "answer",
                            "type": "answer",
                            "position": {"x": 240, "y": 0},
                            "data": {
                                "type": "answer",
                                "label": "合成结果",
                                "reference_key": "answer",
                                "output_type": "template",
                                "output_template": "{{clock.timestamp}}",
                            },
                        },
                    ],
                    "edges": [
                        {
                            "id": "clock-answer",
                            "source": "clock",
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
                release = await request(
                    "GET",
                    path + "/releases/1/integration",
                    params={"project_id": project},
                )
                for _ in range(100):
                    runtime = await runtime_state(path)
                    if runtime["ready"]:
                        assert runtime["scheduler_recent"]
                        assert runtime["worker_recent"] and runtime["database_ready"]
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError("Actual scheduler/queue/schema readiness timed out")
                receipt = await request(
                    "PUT",
                    path + "/schedule",
                    headers=headers,
                    json={
                        "project_id": project,
                        "actor_staff_id": actor,
                        "request_id": str(uuid4()),
                        "expected_revision": 0,
                        "expected_release_id": release["release_id"],
                        "expected_release_digest": release[
                            "definition_digest"
                        ],
                        "enabled": True,
                    },
                )
                return path, receipt["schedule"]

            async def disable(path, plan):
                return await request(
                    "POST",
                    path + "/schedule/disable",
                    headers=headers,
                    json={
                        "project_id": project,
                        "actor_staff_id": actor,
                        "request_id": str(uuid4()),
                        "expected_revision": plan["revision"],
                    },
                )

            # Beat observes a real future minute; Worker consumes Redis.
            worker = start_worker("worker-first")
            beat = processes.start(
                "beat",
                sys.executable,
                [
                    "-m",
                    "celery",
                    "-A",
                    "celery_app.celery",
                    "beat",
                    "--loglevel=warning",
                    "--schedule",
                    str(directory / "beat-state"),
                    "--pidfile",
                    str(directory / "beat.pid"),
                ],
                SERVICE,
                environment,
            )
            path, plan = await create_plan("真实时钟与队列合成验证")
            report["checks"].append("actual_beat_queue_worker_schema_readiness")
            print("PASS: actual Beat probe, queue receipt and schema check", flush=True)
            occurrence, run, count = await wait_run(
                plan["id"], "completed", worker
            )
            assert run.input is None and count == 2
            assert occurrence.scheduled_at.isoformat() in json.dumps(
                run.output
            )
            await request(
                "GET",
                "/v1/workflows/executions/" + run.id,
                params={"project_id": project},
            )
            await disable(path, plan)
            report["checks"].append(
                "real_clock_beat_private_http_redis_worker_and_record"
            )
            print(
                "PASS: actual due clock, private HTTP, Beat and Worker",
                flush=True,
            )
            # Prepare valid enabled plans while their runtime is healthy.
            # The later due facts are synthetic; they must not race the real clock.
            future = datetime.now(timezone.utc) + timedelta(hours=6)
            later_expression = f"{future.minute} {future.hour} * * *"
            prepared = {}
            for name in ("ack-loss", "revoke-after-queue", "disable-after-queue"):
                prepared[name] = await create_plan(name, later_expression)
            processes.stop(beat)
            processes.stop(worker)
            report["worker_pids"] = [worker.pid]

            # Accelerate synthetic due facts only. Subsequent calls are real.
            async def staged(name, action="collect"):
                staged_path, staged_plan = prepared[name]
                async with sessions() as db:
                    await db.execute(
                        update(WorkflowSchedule)
                        .where(WorkflowSchedule.id == staged_plan["id"])
                        .values(
                            next_run_at=datetime.now(timezone.utc).replace(
                                second=0, microsecond=0
                            )
                        )
                    )
                    await db.commit()
                environment["TGO_TIMER_TEST_PLAN"] = staged_plan["id"]
                await short_child(
                    name,
                    sys.executable,
                    [str(Path(__file__)), "--child", action],
                    ROOT,
                )
                state = await snapshot(staged_plan["id"])
                report["last_staged_source"] = (
                    {
                        "status": state[0].status,
                        "error_code": state[0].error_code,
                    }
                    if state[0] is not None
                    else None
                )
                assert state[0] is not None and state[1] is not None
                return staged_path, staged_plan, state

            path_b, plan_b, state_b = await staged("ack-loss", "ack-loss")
            assert (
                state_b[0].last_failure_code == "schedule_broker_ack_unknown"
            )
            async with sessions() as db:
                await db.execute(
                    update(WorkflowExecution)
                    .where(WorkflowExecution.id == state_b[1].id)
                    .values(
                        started_at=datetime.now(timezone.utc)
                        - timedelta(minutes=3)
                    )
                )
                await db.commit()
            await short_child(
                "recover",
                sys.executable,
                [str(Path(__file__)), "--child", "recover"],
                ROOT,
            )
            assert redis.llen(queue) >= 2
            worker = start_worker("worker-restarted")
            _, run_b, count_b = await wait_run(
                plan_b["id"], "completed", worker, 35
            )
            duplicate_marker = "Skipping unclaimable workflow run " + run_b.id
            for _ in range(70):
                observed = (directory / "worker-restarted.log").read_text(
                    encoding="utf-8", errors="replace"
                )
                if duplicate_marker in observed:
                    break
                assert worker.poll() is None
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Duplicate queue delivery was not observed")
            assert (await snapshot(plan_b["id"]))[2] == count_b == 2
            assert run_b.id == state_b[1].id
            await disable(path_b, plan_b)
            report["checks"].append(
                "unknown_ack_recovery_duplicate_queue_messages"
            )
            print(
                "PASS: actual queue redelivery executes only once", flush=True
            )
            report["worker_pids"].append(worker.pid)
            processes.stop(worker)

            path_c, plan_c, _ = await staged("revoke-after-queue")
            await short_child(
                "authority-revoke",
                api_python,
                [str(api_fixture), "revoke"],
                ROOT,
            )
            worker = start_worker("worker-revoked")
            _, run_c, count_c = await wait_run(
                plan_c["id"], "cancelled", worker, 35
            )
            assert (
                count_c == 0 and run_c.input is None and run_c.output is None
            )
            report["checks"].append(
                "queued_work_current_authority_revocation_stops_before_nodes"
            )
            print(
                "PASS: current administrator revocation stops nodes",
                flush=True,
            )
            processes.stop(worker)
            await short_child(
                "authority-grant",
                api_python,
                [str(api_fixture), "grant"],
                ROOT,
            )
            await disable(path_c, plan_c)

            path_d, plan_d, _ = await staged("disable-after-queue")
            await disable(path_d, plan_d)
            worker = start_worker("worker-disabled")
            _, run_d, count_d = await wait_run(
                plan_d["id"], "cancelled", worker, 35
            )
            for _ in range(70):
                result = redis.get("celery-task-meta-" + run_d.id)
                if result and json.loads(result).get("status") == "SUCCESS":
                    break
                assert worker.poll() is None
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Disabled delivery was not consumed")
            assert (await snapshot(plan_d["id"]))[2] == count_d == 0
            assert run_d.input is None and run_d.output is None
            report["checks"].append(
                "http_disable_cancels_pending_redis_delivery"
            )
            print(
                "PASS: disabled queued work is consumed without nodes",
                flush=True,
            )
            for _ in range(100):
                runtime = await runtime_state(path_d)
                if not runtime["scheduler_recent"]:
                    assert not runtime["ready"]
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Stopped Beat evidence did not expire")
            report["checks"].append("stopped_beat_readiness_expires")
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        # Never put SQL/HTTP bodies or infrastructure URLs into the report.
        raise
    finally:
        cleanup_errors = []
        try:
            processes.close()
            report["owned_processes_stopped"] = True
        except Exception:
            cleanup_errors.append("owned_process_cleanup_failed")
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
                cleanup_errors.append("owned_database_cleanup_failed")
        try:
            keys = [queue, "_kombu.binding." + queue]
            keys.extend(
                "celery-task-meta-" + identifier for identifier in task_ids
            )
            keys.extend(redis.scan_iter(match=probe_key(health_scope, "*"), count=50))
            redis.delete(*keys)
            report["owned_queue_removed"] = not bool(redis.exists(queue))
            redis.close()
        except Exception:
            cleanup_errors.append("owned_queue_cleanup_failed")
        await admin.dispose()
        sensitive = [private_key, url.password or ""]
        report["logs_contain_test_secrets"] = any(
            value
            and value in path.read_text(encoding="utf-8", errors="replace")
            for path in directory.glob("*.log")
            for value in sensitive
        )
        report["cleanup_errors"] = cleanup_errors
        report["verification_completed"] = bool(
            report["verification_completed"]
            and not cleanup_errors
            and report["owned_database_removed"]
            and report["owned_processes_stopped"]
            and report["owned_queue_removed"]
            and not report["logs_contain_test_secrets"]
        )
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if cleanup_errors or report["logs_contain_test_secrets"]:
        raise RuntimeError("Verification cleanup or log secret checks failed")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child", choices=["collect", "recover", "ack-loss"])
    arguments = parser.parse_args()
    if arguments.child:
        asyncio.run(child_mode(arguments.child))
    elif arguments.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

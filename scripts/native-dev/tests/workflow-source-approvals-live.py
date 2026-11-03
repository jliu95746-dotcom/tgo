"""Published source approvals through real private HTTP, Redis, Beat and Workers."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
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
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workflow_source_approval_http import SourceApprovalHttp

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.approval import WorkflowApprovalRequest  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402
from app.models.workflow_webhook_delivery import WorkflowWebhookDelivery  # noqa: E402
from app.services.event_source_audit import audit_namespace  # noqa: E402
from app.services.timer_probe import probe_key, probe_scope  # noqa: E402

SOURCE_PATHS = (
    "app/services/node_readiness.py",
    "app/services/approval_preview.py",
    "app/services/webhook_runtime.py",
    "app/services/event_source_audit.py",
    "celery_app/tasks.py",
)


def helpers():
    spec = importlib.util.spec_from_file_location(
        "source_approval_helpers", Path(__file__).with_name("workflow-timers-live.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def hashes():
    return {
        path: hashlib.sha256((SERVICE / path).read_bytes()).hexdigest()
        for path in SOURCE_PATHS
    }


async def child(action):
    helpers().ensure_local()
    database = os.environ["TGO_TIMER_TEST_DATABASE"]
    assert make_url(settings.DATABASE_URL).database == database
    assert re.fullmatch(r"tgo_scenarios_test_timer_[a-f0-9]{32}", database)
    if action == "recover":
        from celery_app.tasks import sweep_pending_resumes

        assert await sweep_pending_resumes() == 1
    else:
        from celery_app.event_source_tasks import audit_waiting_event_sources_task

        audit_waiting_event_sources_task.apply_async(
            task_id=os.environ["TGO_SOURCE_APPROVAL_TASK"],
            queue=settings.WORKFLOW_QUEUE_NAME,
        )


async def main():
    helper = helpers()
    helper.ensure_local()
    nonce = uuid4().hex
    database, queue = "tgo_scenarios_test_timer_" + nonce, "source-approval-" + nonce
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
    private, old, new = (secrets.token_urlsafe(48) for _ in range(3))
    project, actor = str(uuid4()), str(uuid4())
    port, api_port = helper.free_port(), helper.free_port()
    while port == api_port:
        api_port = helper.free_port()
    base, private_base = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{api_port}"
    environment = {
        **os.environ,
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "SECRET_KEY": private,
        "WORKFLOW_QUEUE_NAME": queue,
        "API_INTERNAL_SERVICE_URL": private_base,
        "TGO_TIMER_TEST_DATABASE": database,
        "TGO_TIMER_TEST_PROJECT": project,
        "TGO_TIMER_TEST_ACTOR": actor,
        "TGO_SOURCE_APPROVAL_BINDINGS": str(directory / "source-bindings.json"),
        "TIMER_EXECUTION_ENABLED": "false",
        "WEBHOOK_EXECUTION_ENABLED": "true",
        "DATABASE_ECHO": "false",
        "SAAS_ENABLED": "false",
        "SAAS_BILLING_ENABLED": "false",
        "PYTHONUTF8": "1",
        "WORKFLOW_EVENT_RECEIPT_KEYS": "",
        "WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID": "",
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
        "source_hashes": hashes(),
        "synthetic_closed_facts": True,
        "synthetic_waits": False,
        "owned_processes_stopped": False,
        "owned_database_removed": False,
        "owned_queue_removed": False,
    }
    created, subjects, hook_secrets = False, [], []

    def stage(value):
        report["stage"] = value
        print(json.dumps({"stage": value}), flush=True)

    def rings(keys, active):
        for prefix in ("WORKFLOW_APPROVAL", "WORKFLOW_EVENT_INPUT", "WORKFLOW_WEBHOOK"):
            environment[prefix + "_KEYS"] = json.dumps(keys)
            environment[prefix + "_ACTIVE_KEY_ID"] = active

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

    def workflow_api(name):
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

    async def short(name, arguments, api=False):
        process = processes.start(
            name,
            ROOT / "repos/tgo-api/.venv/Scripts/python.exe" if api else sys.executable,
            arguments,
            ROOT / "repos/tgo-api" if api else SERVICE,
            environment,
        )
        assert await asyncio.to_thread(process.wait, timeout=90) == 0, (
            "Owned child failed: " + name
        )

    async def authority(action):
        await short(
            "authority-" + action,
            [
                str(
                    Path(__file__).with_name(
                        "workflow_source_approval_authority_fixture.py"
                    )
                ),
                action,
            ],
            True,
        )

    async def wait_run(subject, active, predicate):
        for _ in range(240):
            assert active.poll() is None
            async with sessions() as db:
                run = await db.get(WorkflowExecution, subject["run_id"])
                if predicate(run):
                    return run
            await asyncio.sleep(0.25)
        raise RuntimeError(
            "Source approval state observation expired: " + subject["case"]
        )

    async def idle(active):
        for _ in range(240):
            assert active.poll() is None
            if not broker.llen(queue) and not any(
                json.loads(value)[2] == queue for value in broker.hvals("unacked")
            ):
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("Owned source Worker did not become idle")

    async def audit(active, expected):
        identifier = "source-approval-audit-" + uuid4().hex
        environment["TGO_SOURCE_APPROVAL_TASK"] = identifier
        await short(identifier, [str(Path(__file__).resolve()), "--child", "audit"])
        for _ in range(160):
            result = broker.get("celery-task-meta-" + identifier)
            if result and json.loads(result).get("status") == "SUCCESS":
                assert json.loads(result)["result"] == expected
                await idle(active)
                return
            assert active.poll() is None
            await asyncio.sleep(0.25)
        raise RuntimeError("Actual source audit task did not finish")

    async def assert_stopped(subject, approved=False):
        async with sessions() as db:
            run = await db.get(WorkflowExecution, subject["run_id"])
            approval = await db.get(WorkflowApprovalRequest, subject["approval_id"])
            assert (
                run.status == "cancelled" and run.input is None and run.output is None
            )
            assert approval.status == ("approved" if approved else "cancelled")
            assert approval.checkpoint["capture_status"] == "redacted"
            nodes = list(
                await db.scalars(
                    select(NodeExecution.node_id).where(
                        NodeExecution.execution_id == run.id
                    )
                )
            )
            assert sorted(nodes) == ["approval", "source"]
            if subject["kind"] == "webhook":
                delivery = await db.get(WorkflowWebhookDelivery, subject["delivery_id"])
                assert (
                    delivery.payload_ciphertext is None
                    and delivery.status == "cancelled"
                )

    rings({"old": old}, "old")
    try:
        stage("create_owned_database_and_staff_authority")
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
        await authority("seed")
        api = processes.start(
            "private-api",
            ROOT / "repos/tgo-api/.venv/Scripts/python.exe",
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
        workflow = workflow_api("workflow-api-old")
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            http = SourceApprovalHttp(client, base, project, actor, private)
            hook_secrets = http.hook_secrets
            await http.ready(api, private_base)
            await http.ready(workflow)
            stage("publish_nine_real_source_approval_flows")
            events = [
                await http.create("event", case)
                for case in (
                    "happy",
                    "recovery",
                    "disabled",
                    "rebound",
                    "cleared",
                    "staff_revoked",
                )
            ]
            environment["TGO_SOURCE_APPROVAL_CASES"] = json.dumps(events)
            await authority("bind")
            events = json.loads(
                (directory / "source-bindings.json").read_text(encoding="utf-8")
            )
            hooks = [
                await http.create("webhook", case)
                for case in ("happy", "disabled", "revoked")
            ]
            report["checks"].append(
                "nine_real_published_source_approval_flows_with_actual_private_authority"
            )
            active = worker("worker-old")
            for row in events:
                accepted = await http.send_event(row)
                row.update(kind="event", run_id=accepted["execution_id"])
                subjects.append(row)
            for row in hooks:
                accepted = await http.send_webhook(row)
                delivery_id = str(accepted["delivery_id"])
                from app.services.webhook_state import execution_id

                row.update(
                    kind="webhook",
                    run_id=execution_id(delivery_id),
                    delivery_id=delivery_id,
                )
                subjects.append(row)
            stage("all_nine_workers_reach_real_encrypted_approval_waits")
            for row in subjects:
                run = await wait_run(
                    row,
                    active,
                    lambda value: value is not None
                    and value.status == "waiting_approval",
                )
                row["approval_id"] = run.active_approval_id
                row["frozen_input"] = run.input
                async with sessions() as db:
                    request = await db.get(
                        WorkflowApprovalRequest, run.active_approval_id
                    )
                    assert request.checkpoint["key_id"] == "old"
                    row["frozen_checkpoint"] = dict(request.checkpoint)
            report["checks"].append(
                "nine_real_source_runs_pause_with_encrypted_checkpoints_and_frozen_inputs"
            )
            for row in (events[0], hooks[0]):
                assert await http.approve(row["run_id"]) == row["approval_id"]
                run = await wait_run(
                    row, active, lambda value: value.status == "completed"
                )
                assert run.output == {"result": "synthetic-" + row["kind"] + "-happy"}
                assert run.input is None
            assert (await http.send_event(events[0]))["deduplicated"]
            assert (await http.send_webhook(hooks[0]))["deduplicated"]
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "event_and_webhook_confirmations_resume_exact_payload_and_replays_deduplicate"
            )

            rings({"new": new}, "new")
            processes.stop(workflow)
            workflow = workflow_api("workflow-api-new-only")
            await http.ready(workflow)
            active = worker("worker-no-old-keys")
            beat = processes.start(
                "beat-no-old-keys",
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
            waiting = [row for row in subjects if row not in (events[0], hooks[0])]
            stage("natural_beat_rechecks_seven_waits_without_payload_or_approval_keys")
            for _ in range(220):
                assert active.poll() is None and beat.poll() is None
                if broker.get(namespace + ":cursor") == max(
                    row["run_id"] for row in waiting
                ).encode() and not broker.exists(namespace + ":lease"):
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Natural source approval audit observation expired")
            processes.stop(beat)
            await idle(active)
            async with sessions() as db:
                for row in waiting:
                    run = await db.get(WorkflowExecution, row["run_id"])
                    request = await db.get(WorkflowApprovalRequest, row["approval_id"])
                    assert (
                        run.status == "waiting_approval"
                        and run.input == row["frozen_input"]
                    )
                    assert (
                        request.checkpoint == row["frozen_checkpoint"]
                        and request.status == "pending"
                    )
            report["checks"].append(
                "natural_beat_preserves_seven_authorized_waits"
                "_without_decrypting_any_body_or_checkpoint"
            )
            recovery = events[1]
            await http.approve(recovery["run_id"])
            await wait_run(
                recovery,
                active,
                lambda value: value.status == "pending_resume"
                and value.error is not None,
            )
            await idle(active)
            processes.stop(active)
            rings({"old": old, "new": new}, "new")
            active = worker("worker-restored-keys")
            async with sessions() as db:
                approval = await db.get(
                    WorkflowApprovalRequest, recovery["approval_id"]
                )
                approval.last_resume_dispatch_at = datetime.now(
                    timezone.utc
                ) - timedelta(minutes=2)
                await db.commit()
            await short(
                "recover-original-event",
                [str(Path(__file__).resolve()), "--child", "recover"],
            )
            run = await wait_run(
                recovery, active, lambda value: value.status == "completed"
            )
            assert (
                run.output == {"result": "synthetic-event-recovery"}
                and run.error is None
                and run.input is None
            )
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "real_source_event_retains_unreadable_approved_input"
                "_then_recovers_original_cursor_once"
            )
            rings({"new": new}, "new")
            active = worker("worker-revocation-no-old-keys")
            stage("revoke_sources_with_missing_old_body_and_checkpoint_keys")
            for action, case in (
                ("disable", "disabled"),
                ("rebind", "rebound"),
                ("clear", "cleared"),
            ):
                await authority(action)
                await audit(active, 1)
                await assert_stopped(next(row for row in events if row["case"] == case))
            report["checks"].append(
                "actual_event_disable_rebind_and_memory_clear_each_cancel_only_the_matching_wait"
            )
            await http.disable_webhook(hooks[1])
            await assert_stopped(hooks[1])
            report["checks"].append(
                "actual_webhook_disable_cancels_real_wait_and_payload_without_retained_keys"
            )
            await http.approve(hooks[2]["run_id"])
            await wait_run(
                hooks[2],
                active,
                lambda value: value.status == "pending_resume"
                and value.error is not None,
            )
            await idle(active)
            await authority("revoke")
            await audit(active, 2)
            await assert_stopped(events[-1])
            await assert_stopped(hooks[2], approved=True)
            report["checks"].append(
                "actual_admin_revocation_cancels_waiting_event"
                "_and_approved_webhook_before_decryption"
            )
            async with sessions() as db:
                runs = list(await db.scalars(select(WorkflowExecution)))
                requests = list(await db.scalars(select(WorkflowApprovalRequest)))
                nodes = list(await db.scalars(select(NodeExecution)))
                deliveries = list(await db.scalars(select(WorkflowWebhookDelivery)))
                assert len(runs) == 9 and all(run.input is None for run in runs)
                assert (
                    sum(run.status == "completed" for run in runs) == 3
                    and sum(run.status == "cancelled" for run in runs) == 6
                )
                assert len(requests) == 9 and all(
                    row.checkpoint["capture_status"] == "redacted" for row in requests
                )
                assert (
                    sum(row.status == "approved" for row in requests) == 4
                    and sum(row.status == "cancelled" for row in requests) == 5
                )
                assert (
                    len(nodes) == 21
                    and len({(row.execution_id, row.node_id) for row in nodes}) == 21
                )
                assert all(row.payload_ciphertext is None for row in deliveries)
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "nine_terminal_runs_keep_decision_history_scrub_bodies_and_execute_each_node_once"
            )
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise RuntimeError(
            "Source approval verification failed at "
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
            ] + [probe_key(scope, kind) for kind in ("beat", "challenge", "worker")]
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
                not broker.exists(queue, namespace + ":lease", namespace + ":cursor")
                and report["owned_unacked_remaining"] == 0
            )
            broker.close()
        except Exception:
            errors.append("owned_queue_cleanup_failed")
        await admin.dispose()
        bindings = directory / "source-bindings.json"
        if bindings.exists():
            bindings.unlink()
        report["logs_contain_test_secrets"] = any(
            value and value in log.read_text(encoding="utf-8", errors="replace")
            for log in directory.glob("*.log")
            for value in [private, old, new, url.password or "", *hook_secrets]
        )
        report["cleanup_errors"] = errors
        report["production_sources_unchanged"] = report["source_hashes"] == hashes()
        report["verification_completed"] = bool(
            report["verification_completed"]
            and not errors
            and report["owned_database_removed"]
            and report["owned_processes_stopped"]
            and report["owned_queue_removed"]
            and not report["logs_contain_test_secrets"]
            and report["production_sources_unchanged"]
        )
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        (ROOT / ".tmp/native-dev/workflow-source-approvals-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child", choices=["audit", "recover"])
    args = parser.parse_args()
    if args.child:
        asyncio.run(child(args.child))
    elif args.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

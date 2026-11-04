"""Prove retained checkpoint keys and exact continuation in real processes."""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import hashlib
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

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.approval import WorkflowApprovalRequest  # noqa: E402
from app.models.execution import NodeExecution, WorkflowExecution  # noqa: E402


def helpers():
    spec = importlib.util.spec_from_file_location(
        "approval_key_helpers", Path(__file__).with_name("workflow-timers-live.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def child(action):
    helpers().ensure_local()
    database = os.environ["TGO_TIMER_TEST_DATABASE"]
    assert make_url(settings.DATABASE_URL).database == database
    assert re.fullmatch(r"tgo_scenarios_test_timer_[a-f0-9]{32}", database)
    if action == "recover":
        from celery_app.tasks import sweep_pending_resumes

        assert await sweep_pending_resumes() == 1
    else:
        from celery_app.tasks import execute_workflow_task

        execute_workflow_task.apply_async(
            args=[
                os.environ["TGO_APPROVAL_KEY_RUN"],
                os.environ["TGO_APPROVAL_KEY_FLOW"],
                {},
            ],
            kwargs={"project_id": os.environ["TGO_TIMER_TEST_PROJECT"]},
            task_id=os.environ["TGO_APPROVAL_KEY_TASK"],
            queue=settings.WORKFLOW_QUEUE_NAME,
        )


async def main():
    helper = helpers()
    helper.ensure_local()
    nonce = uuid4().hex
    database, queue = "tgo_scenarios_test_timer_" + nonce, "approval-keys-" + nonce
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
        "TGO_TIMER_TEST_DATABASE": database,
        "TGO_TIMER_TEST_PROJECT": project,
        "TGO_TIMER_TEST_ACTOR": actor,
        "SECRET_KEY": private,
        "WORKFLOW_QUEUE_NAME": queue,
        "API_INTERNAL_SERVICE_URL": private_base,
        "WORKFLOW_APPROVAL_KEYS": json.dumps({"old": old}),
        "WORKFLOW_APPROVAL_ACTIVE_KEY_ID": "old",
        "TIMER_EXECUTION_ENABLED": "false",
        "WEBHOOK_EXECUTION_ENABLED": "false",
        "DATABASE_ECHO": "false",
        "SAAS_ENABLED": "false",
        "SAAS_BILLING_ENABLED": "false",
        "PYTHONUTF8": "1",
    }
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
        "owned_processes_stopped": False,
        "owned_database_removed": False,
        "owned_queue_removed": False,
        "database": database,
        "run_directory": str(directory),
        "real_customer_or_model_calls": False,
    }

    def source_hashes():
        return {
            path: hashlib.sha256((SERVICE / path).read_bytes()).hexdigest()
            for path in (
                "app/services/approval_checkpoint.py",
                "app/services/approval_state.py",
            )
        }

    report["source_hashes"] = source_hashes()
    created, run_ids, resume_ids = False, [], []

    def stage(value):
        report["stage"] = value
        print(json.dumps({"stage": value}), flush=True)

    def ring(keys, active):
        environment["WORKFLOW_APPROVAL_KEYS"] = json.dumps(keys)
        environment["WORKFLOW_APPROVAL_ACTIVE_KEY_ID"] = active

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

    async def short_child(name, arguments, api=False):
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
        await short_child(
            "authority-" + action,
            [
                str(
                    Path(__file__).with_name(
                        "workflow_approval_key_authority_fixture.py"
                    )
                ),
                action,
            ],
            True,
        )

    async def ready(client, process, address):
        for _ in range(240):
            assert process.poll() is None
            try:
                if (await client.get(address + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned API readiness observation expired")

    async def wait_run(identifier, active, predicate):
        for _ in range(240):
            assert active.poll() is None
            async with sessions() as db:
                run = await db.get(WorkflowExecution, identifier)
                if run is not None and predicate(run):
                    return run
            await asyncio.sleep(0.25)
        raise RuntimeError("Owned approval state observation expired")

    async def idle(active):
        for _ in range(240):
            assert active.poll() is None
            if not broker.llen(queue) and not any(
                json.loads(value)[2] == queue for value in broker.hvals("unacked")
            ):
                return
            await asyncio.sleep(0.25)
        raise RuntimeError("Owned Worker did not become idle")

    async def node_ids(identifier):
        async with sessions() as db:
            return list(
                await db.scalars(
                    select(NodeExecution.node_id)
                    .where(NodeExecution.execution_id == identifier)
                    .order_by(NodeExecution.started_at, NodeExecution.id)
                )
            )

    try:
        stage("create_owned_database_and_actual_staff_authority")
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
            await ready(client, api, private_base)
            await ready(client, workflow, base)

            async def request(method, path, expected=200, **kwargs):
                response = await client.request(method, base + path, **kwargs)
                assert (
                    response.status_code == expected
                ), f"Workflow HTTP {response.status_code}: {path}"
                return response.json()

            nodes = [
                {
                    "id": "input",
                    "type": "input",
                    "position": {"x": 0, "y": 0},
                    "data": {
                        "type": "input",
                        "reference_key": "input",
                        "label": "合成业务问题",
                        "input_variables": [
                            {"name": "question", "type": "string", "required": True}
                        ],
                    },
                }
            ]
            for index in (1, 2):
                nodes.append(
                    {
                        "id": f"approval{index}",
                        "type": "approval",
                        "position": {"x": 200 * index, "y": 0},
                        "data": {
                            "type": "approval",
                            "reference_key": f"approval{index}",
                            "label": "合成确认",
                            "approver_staff_id": actor,
                            "summary": "确认原问题 {{input.question}}",
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
                        "reference_key": "answer",
                        "label": "原问题返回",
                        "output_type": "variable",
                        "output_variable": "input.question",
                    },
                }
            )
            flow = await request(
                "POST",
                "/v1/workflows/",
                201,
                params={"project_id": project},
                json={
                    "name": "审批保管键真实进程恢复",
                    "nodes": nodes,
                    "edges": [
                        {
                            "id": f"edge{i}",
                            "source": nodes[i]["id"],
                            "target": nodes[i + 1]["id"],
                        }
                        for i in range(3)
                    ],
                },
            )
            path = "/v1/workflows/" + flow["id"]
            environment["TGO_APPROVAL_KEY_FLOW"] = flow["id"]
            await request(
                "POST",
                path + "/publish",
                params={"project_id": project},
                json={
                    "expected_draft_version": flow["version"],
                    "expected_publication_revision": flow["publication_revision"],
                },
            )
            release = await request(
                "GET", path + "/releases/1/integration", params={"project_id": project}
            )

            async def execute(question):
                row = await request(
                    "POST",
                    path + "/execute",
                    params={"project_id": project},
                    json={
                        "inputs": {"question": question},
                        "async": True,
                        "execution_mode": "production",
                        "release_version": 1,
                        "expected_release_id": release["release_id"],
                        "expected_release_digest": release["definition_digest"],
                    },
                )
                run_ids.append(row["id"])
                return row["id"]

            async def approve(identifier, node):
                query = {"project_id": project, "actor_staff_id": actor}
                headers = {"X-Internal-API-Key": private}
                summaries = await request(
                    "GET", "/v1/workflows/approvals", params=query, headers=headers
                )
                summary = next(
                    row
                    for row in summaries
                    if row["execution_id"] == identifier and row["node_id"] == node
                )
                result = await request(
                    "POST",
                    "/v1/workflows/approvals/" + summary["id"] + "/decision",
                    params=query,
                    headers=headers,
                    json={
                        "decision": "approve",
                        "preview_digest": summary["preview_digest"],
                    },
                )
                assert (
                    result["status"] == "approved"
                    and result["dispatch_status"] == "queued"
                )
                resume_ids.append(identifier + ":approval:" + summary["id"])
                return summary["id"]

            stage("old_worker_seals_first_real_checkpoint")
            identifier = await execute("synthetic-original-private-question")
            active = worker("worker-old")
            run = await wait_run(
                identifier, active, lambda row: row.status == "waiting_approval"
            )
            started, first_id = run.started_at, run.active_approval_id
            async with sessions() as db:
                first = await db.get(WorkflowApprovalRequest, first_id)
                frozen = dict(first.checkpoint)
                assert frozen[
                    "key_id"
                ] == "old" and "synthetic-original-private-question" not in json.dumps(
                    frozen
                )
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "old_worker_seals_real_checkpoint_and_keeps_body_confidential"
            )
            ring({"new": new}, "new")
            processes.stop(workflow)
            workflow = workflow_api("workflow-api-new-only")
            await ready(client, workflow, base)
            assert await approve(identifier, "approval1") == first_id
            active = worker("worker-missing-old")
            stage("missing_retained_key_preserves_approved_cursor")
            run = await wait_run(
                identifier,
                active,
                lambda row: row.status == "pending_resume" and row.error is not None,
            )
            assert "密钥" in run.error and "保留" in run.error
            assert run.started_at == started and run.active_approval_id == first_id
            public = await request(
                "GET",
                "/v1/workflows/executions/" + identifier,
                params={"project_id": project},
            )
            assert public["status"] == "pending_resume" and public["error"] == run.error
            public_text = json.dumps(public)
            assert (
                old not in public_text
                and new not in public_text
                and "ciphertext" not in public_text
            )
            report["checks"].append(
                "execution_http_exposes_safe_key_recovery_reason_without_checkpoint_or_keys"
            )
            async with sessions() as db:
                first = await db.get(WorkflowApprovalRequest, first_id)
                assert first.status == "approved" and first.checkpoint == frozen
                decided_at, decided_by = first.decided_at, first.decided_by
            assert await node_ids(identifier) == ["input", "approval1"]
            await idle(active)
            result = json.loads(broker.get("celery-task-meta-" + resume_ids[-1]))
            assert result["status"] == "SUCCESS"
            processes.stop(active)
            report["checks"].append(
                "missing_old_key_preserves_same_run_decision_ciphertext"
                "_and_nodes_without_task_failure"
            )

            ring({"old": old, "new": new}, "new")
            active = worker("worker-restored-old")
            async with sessions() as db:
                first = await db.get(WorkflowApprovalRequest, first_id)
                first.last_resume_dispatch_at = datetime.now(timezone.utc) - timedelta(
                    minutes=2
                )
                await db.commit()
            stage("restore_retained_key_and_recover_original_cursor")
            await short_child(
                "recover-original",
                [str(Path(__file__).resolve()), "--child", "recover"],
            )
            run = await wait_run(
                identifier, active, lambda row: row.status == "waiting_approval"
            )
            second_id = run.active_approval_id
            assert (
                second_id != first_id
                and run.started_at == started
                and run.error is None
            )
            assert await node_ids(identifier) == ["input", "approval1", "approval2"]
            async with sessions() as db:
                second = await db.get(WorkflowApprovalRequest, second_id)
                assert (
                    second.status == "pending" and second.checkpoint["key_id"] == "new"
                )
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "restored_worker_resumes_old_cursor_once"
                "_and_seals_next_approval_with_new_active_key"
            )
            ring({"new": new}, "new")
            assert await approve(identifier, "approval2") == second_id
            active = worker("worker-new-only")
            stage("new_only_worker_finishes_original_run_without_replay")
            run = await wait_run(
                identifier, active, lambda row: row.status == "completed"
            )
            assert (
                run.output == {"result": "synthetic-original-private-question"}
                and run.input is None
                and run.started_at == started
            )
            assert await node_ids(identifier) == [
                "input",
                "approval1",
                "approval2",
                "answer",
            ]
            async with sessions() as db:
                approvals = list(
                    await db.scalars(
                        select(WorkflowApprovalRequest).where(
                            WorkflowApprovalRequest.execution_id == identifier
                        )
                    )
                )
                assert len(approvals) == 2 and all(
                    row.status == "approved"
                    and row.checkpoint["capture_status"] == "redacted"
                    for row in approvals
                )
                first = await db.get(WorkflowApprovalRequest, first_id)
                assert first.decided_at == decided_at and first.decided_by == decided_by
            environment.update(
                {
                    "TGO_APPROVAL_KEY_RUN": identifier,
                    "TGO_APPROVAL_KEY_TASK": "approval-key-duplicate-" + uuid4().hex,
                }
            )
            await short_child(
                "duplicate-completed",
                [str(Path(__file__).resolve()), "--child", "duplicate"],
            )
            for _ in range(160):
                result = broker.get(
                    "celery-task-meta-" + environment["TGO_APPROVAL_KEY_TASK"]
                )
                if result and json.loads(result).get("status") == "SUCCESS":
                    break
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Owned duplicate task did not complete")
            assert await node_ids(identifier) == [
                "input",
                "approval1",
                "approval2",
                "answer",
            ]
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "new_only_worker_completes_original_input_once_and_preserves_approved_history"
            )

            ring({"old": old}, "old")
            active = worker("worker-old-revocation-fixture")
            revoked = await execute("synthetic-revocation-question")
            run = await wait_run(
                revoked, active, lambda row: row.status == "waiting_approval"
            )
            revoked_approval = run.active_approval_id
            await idle(active)
            processes.stop(active)
            ring({"new": new}, "new")
            assert await approve(revoked, "approval1") == revoked_approval
            active = worker("worker-missing-before-revocation")
            await wait_run(
                revoked,
                active,
                lambda row: row.status == "pending_resume" and row.error is not None,
            )
            await idle(active)
            processes.stop(active)
            await authority("disable")
            response = await client.post(
                private_base + "/internal/workflows/approvals/assignees/verify",
                json={"project_id": project, "assignee_ids": [actor]},
                headers={"X-Internal-API-Key": private},
            )
            assert response.status_code == 409
            stage("revoked_staff_stops_even_when_old_checkpoint_key_is_missing")
            async with sessions() as db:
                approval = await db.get(WorkflowApprovalRequest, revoked_approval)
                approval.last_resume_dispatch_at = datetime.now(
                    timezone.utc
                ) - timedelta(minutes=2)
                await db.commit()
            await short_child(
                "recover-revoked", [str(Path(__file__).resolve()), "--child", "recover"]
            )
            active = worker("worker-revoked-new-only")
            run = await wait_run(revoked, active, lambda row: row.status == "failed")
            assert run.input is None and "权限" in run.error
            async with sessions() as db:
                approval = await db.get(WorkflowApprovalRequest, revoked_approval)
                assert (
                    approval.status == "approved"
                    and approval.checkpoint["capture_status"] == "redacted"
                )
            assert await node_ids(revoked) == ["input", "approval1"]
            await idle(active)
            processes.stop(active)
            report["checks"].append(
                "actual_staff_revocation_cleans_unreadable_cursor_before_any_resume_node"
            )
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise RuntimeError(
            "Approval key verification failed at "
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
            keys = [queue, "_kombu.binding." + queue] + [
                "celery-task-meta-" + identifier for identifier in run_ids + resume_ids
            ]
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
                not broker.exists(queue) and report["owned_unacked_remaining"] == 0
            )
            broker.close()
        except Exception:
            errors.append("owned_queue_cleanup_failed")
        await admin.dispose()
        report["logs_contain_test_secrets"] = any(
            secret and secret in log.read_text(encoding="utf-8", errors="replace")
            for log in directory.glob("*.log")
            for secret in (private, old, new, url.password or "")
        )
        report["cleanup_errors"] = errors
        report["production_sources_unchanged"] = (
            report["source_hashes"] == source_hashes()
        )
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
        (ROOT / ".tmp/native-dev/workflow-approval-keys-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--child", choices=["recover", "duplicate"])
    args = parser.parse_args()
    if args.child:
        asyncio.run(child(args.child))
    elif args.run_local:
        asyncio.run(main())
    else:
        parser.error("Explicit --run-local is required")

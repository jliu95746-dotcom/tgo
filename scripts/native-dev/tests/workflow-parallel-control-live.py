"""Actual Workflow HTTP/Worker/PG with an explicitly synthetic loopback AI hop."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
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

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.execution import WorkflowExecution, NodeExecution  # noqa: E402

SOURCE_PATHS = (
    "app/engine/executor.py",
    "app/engine/parallel_control.py",
    "app/services/parallel_node_receipts.py",
    "app/api/executions.py",
    "celery_app/tasks.py",
)


def hashes():
    return {
        name: hashlib.sha256((SERVICE / name).read_bytes()).hexdigest() for name in SOURCE_PATHS
    }


def helpers():
    spec = importlib.util.spec_from_file_location(
        "parallel_owned_helpers",
        Path(__file__).with_name("workflow-timers-live.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def graph(case):
    count = 5 if case == "limit" else 2
    nodes = [
        {
            "id": "input",
            "type": "input",
            "data": {
                "type": "input",
                "reference_key": "input",
                "label": "合成输入",
                "input_variables": [{"name": "case", "type": "string", "required": True}],
            },
        },
        {
            "id": "fork",
            "type": "parallel",
            "data": {
                "type": "parallel",
                "reference_key": "fork",
                "label": "并行核验",
                "branches": count,
                "wait_for_all": True,
                "timeout": 1 if case == "timeout" else 20,
            },
        },
    ]
    edges = [{"id": "i-f", "source": "input", "target": "fork"}]
    refs = ["first", "second"] if count == 2 else ["b" + str(i) for i in range(count)]
    for index, ref in enumerate(refs):
        nodes.append(
            {
                "id": ref,
                "type": "llm",
                "data": {
                    "type": "llm",
                    "reference_key": ref,
                    "label": "合成分支" + str(index),
                    "provider_id": "owned-fixture",
                    "model_id": "synthetic",
                    "max_tokens": 32,
                    "user_prompt": "{{input.case}}:" + ref,
                },
            }
        )
        edges += [
            {
                "id": "f-" + ref,
                "source": "fork",
                "target": ref,
                "sourceHandle": "branch-" + str(index),
            },
            {"id": ref + "-j", "source": ref, "target": "join"},
        ]
    if case == "success":
        nodes.append(
            {
                "id": "followup",
                "type": "llm",
                "data": {
                    "type": "llm",
                    "reference_key": "followup",
                    "label": "本分支后一步",
                    "provider_id": "owned-fixture",
                    "model_id": "synthetic",
                    "max_tokens": 32,
                    "user_prompt": "{{input.case}}:followup-{{first.text}}",
                },
            }
        )
        next(edge for edge in edges if edge["id"] == "first-j")["target"] = "followup"
        edges.append({"id": "followup-j", "source": "followup", "target": "join"})
        refs[0] = "followup"
    nodes.append(
        {
            "id": "join",
            "type": "answer",
            "data": {
                "type": "answer",
                "reference_key": "join",
                "label": "确定汇合",
                "output_type": "template",
                "output_template": " + ".join("{{" + ref + ".text}}" for ref in refs),
            },
        }
    )
    for index, node in enumerate(nodes):
        node["position"] = {"x": index * 240, "y": 0}
    return {"name": "合成并行-" + case, "nodes": nodes, "edges": edges}


async def main():
    helper = helpers()
    helper.ensure_local()
    nonce = uuid4().hex
    database, queue = "tgo_scenarios_test_timer_" + nonce, "parallel-control-" + nonce
    project, private = str(uuid4()), secrets.token_urlsafe(48)
    directory = ROOT / ".tmp/native-dev" / queue
    directory.mkdir(parents=True, exist_ok=False)
    processes = helper.OwnedProcesses(directory)
    url = make_url(settings.DATABASE_URL)
    admin = create_async_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True
    )
    target = url.set(database=database)
    engine = create_async_engine(target, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    broker = Redis.from_url(settings.REDIS_URL)
    api_port, fixture_port = helper.free_port(), helper.free_port()
    while api_port == fixture_port:
        fixture_port = helper.free_port()
    base, fixture_base = f"http://127.0.0.1:{api_port}", f"http://127.0.0.1:{fixture_port}"
    environment = {
        **os.environ,
        "DATABASE_URL": target.render_as_string(hide_password=False),
        "WORKFLOW_QUEUE_NAME": queue,
        "SECRET_KEY": private,
        "AI_SERVICE_URL": fixture_base,
        "TGO_PARALLEL_PROJECT": project,
        "TIMER_EXECUTION_ENABLED": "false",
        "WEBHOOK_EXECUTION_ENABLED": "false",
        "DATABASE_ECHO": "false",
        "PYTHONUTF8": "1",
    }
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
        "database": database,
        "run_directory": str(directory),
        "source_hashes": hashes(),
        "run_evidence": [],
        "synthetic_ai_only": True,
        "owned_database_removed": False,
        "owned_processes_stopped": False,
        "owned_queue_removed": False,
    }
    created = False
    worker = None
    completed_success = None

    def stage(value):
        report["stage"] = value
        print(json.dumps({"stage": value}), flush=True)

    async def ready(client, process, address):
        for _ in range(180):
            assert process.poll() is None
            try:
                if (await client.get(address + "/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        raise RuntimeError("Owned API did not become ready")

    async def wait_run(flow_id, predicate):
        for _ in range(200):
            if worker is not None:
                assert worker.poll() is None, "The same owned Worker has stopped"
            async with sessions() as db:
                run = await db.scalar(
                    select(WorkflowExecution).where(WorkflowExecution.workflow_id == flow_id)
                )
                rows = (
                    []
                    if run is None
                    else list(
                        (
                            await db.scalars(
                                select(NodeExecution).where(NodeExecution.execution_id == run.id)
                            )
                        ).all()
                    )
                )
                if run is not None and predicate(run, rows):
                    return run, rows
            await asyncio.sleep(0.1)
        raise RuntimeError("Owned run observation expired")

    async def wait_calls(client, case, *, disconnected=False):
        for _ in range(150):
            state = (await client.get(fixture_base + "/fixture/state")).json()["calls"]
            rows = [row for row in state if row["case"] == case]
            if len(rows) >= 2 and (not disconnected or all(row["disconnected_at"] for row in rows)):
                return rows
            await asyncio.sleep(0.1)
        raise RuntimeError("Owned synthetic HTTP calls did not settle")

    def evidence(case, run, rows):
        return {
            "case": case,
            "execution_id": run.id,
            "status": run.status,
            "input_cleared": run.input is None,
            "nodes": [
                {
                    "node_id": row.node_id,
                    "status": row.status,
                    "started_at": row.started_at.isoformat(),
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "duration_ms": row.duration,
                }
                for row in rows
            ],
        }

    try:
        stage("migrate_owned_database_and_start_synthetic_ai_and_actual_workflow")
        assert broker.ping()
        async with admin.connect() as db:
            assert not await db.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"), {"name": database}
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
        fixture = processes.start(
            "synthetic-ai",
            sys.executable,
            [
                "-m",
                "uvicorn",
                "workflow_parallel_fixture:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(fixture_port),
                "--no-access-log",
            ],
            Path(__file__).parent,
            environment,
        )
        api = processes.start(
            "workflow-api",
            sys.executable,
            [
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
                "--no-access-log",
            ],
            SERVICE,
            environment,
        )
        async with httpx.AsyncClient(timeout=35, trust_env=False) as client:
            await ready(client, fixture, fixture_base)
            await ready(client, api, base)
            flows = {}
            for case in ("success", "limit", "failure", "timeout", "worker", "sync", "stream"):
                response = await client.post(
                    base + "/v1/workflows/", params={"project_id": project}, json=graph(case)
                )
                assert response.status_code == 201, "Create owned graph HTTP " + str(
                    response.status_code
                )
                flow = response.json()
                response = await client.post(
                    base + "/v1/workflows/" + flow["id"] + "/publish",
                    params={"project_id": project},
                    json={
                        "expected_draft_version": flow["version"],
                        "expected_publication_revision": flow["publication_revision"],
                    },
                )
                assert response.status_code == 200, "Publish owned graph HTTP " + str(
                    response.status_code
                )
                flows[case] = flow["id"]
            report["checks"].append("seven_actual_published_parallel_graphs_without_paid_providers")
            worker = processes.start(
                "worker",
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
                    "--hostname=" + queue,
                    "-Q",
                    queue,
                ],
                SERVICE,
                environment,
            )

            def request(case, *, stream=False):
                return {
                    "inputs": {"case": case},
                    "execution_mode": "production",
                    "release_version": 1,
                    "async": case not in {"sync", "stream"},
                    "stream": stream,
                }

            async def dispatch(case):
                response = await client.post(
                    base + "/v1/workflows/" + flows[case] + "/execute",
                    params={"project_id": project},
                    json=request(case),
                )
                assert response.status_code == 200, "Dispatch owned graph HTTP " + str(
                    response.status_code
                )

            for case in ("success", "limit", "failure", "timeout"):
                stage("real_worker_" + case)
                await dispatch(case)
                run, rows = await wait_run(
                    flows[case], lambda row, nodes: row.status in {"completed", "failed"}
                )
                assert run.input is None
                branches = [row for row in rows if row.node_type == "llm"]
                report["run_evidence"].append(evidence(case, run, rows))
                if case == "success":
                    completed_success = (run.id, len(rows))
                assert len({row.node_id for row in rows}) == len(rows)
                if case in {"success", "limit"}:
                    expected = (
                        "followup-first + second" if case == "success" else "b0 + b1 + b2 + b3 + b4"
                    )
                    assert run.status == "completed" and run.output == {"result": expected}
                    overlapping = (
                        branches
                        if case == "limit"
                        else [row for row in branches if row.node_id in {"first", "second"}]
                    )
                    assert max(row.started_at for row in overlapping) < min(
                        row.completed_at for row in overlapping
                    )
                    report["checks"].append(
                        case + "_real_http_overlap_and_deterministic_persisted_join"
                    )
                else:
                    assert run.status == "failed" and not run.output
                    assert not any(row.node_id == "join" for row in rows)
                    assert len(branches) == 2
                    assert {row.status for row in branches} == (
                        {"failed", "cancelled"} if case == "failure" else {"cancelled"}
                    )
                    assert "private-synthetic-provider-body" not in (run.error or "")
                    report["checks"].append(case + "_cancels_http_siblings_without_false_join")

            for case in ("worker", "sync", "stream"):
                stage("cancel_real_" + case + "_while_both_http_calls_are_blocked")
                frames = []

                async def streaming():
                    async with client.stream(
                        "POST",
                        base + "/v1/workflows/" + flows[case] + "/execute",
                        params={"project_id": project},
                        json=request(case, stream=True),
                    ) as response:
                        assert response.status_code == 200
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                frames.append(json.loads(line[6:]))

                task = asyncio.create_task(streaming() if case == "stream" else dispatch(case))
                try:
                    await wait_calls(client, case)
                    run, _ = await wait_run(flows[case], lambda row, nodes: row.status == "running")
                    denied = await client.post(
                        base + "/v1/workflows/executions/" + run.id + "/cancel",
                        params={"project_id": str(uuid4())},
                    )
                    assert denied.status_code == 404
                    cancel_requested_at = datetime.now(timezone.utc).isoformat()
                    response = await client.post(
                        base + "/v1/workflows/executions/" + run.id + "/cancel",
                        params={"project_id": project},
                    )
                    assert response.status_code == 200
                    stopped, rows = await wait_run(
                        flows[case],
                        lambda row, nodes: row.status == "cancelled"
                        and len([node for node in nodes if node.status == "cancelled"]) == 2,
                    )
                    await asyncio.wait_for(task, 10)
                    calls = await wait_calls(client, case, disconnected=True)
                    assert len(calls) == 2
                    saved = evidence(case, stopped, rows)
                    saved["cancel_requested_at"] = cancel_requested_at
                    saved["wrong_tenant_cancel_denied"] = True
                    report["run_evidence"].append(saved)
                    assert stopped.input is None and not stopped.output
                    assert not any(row.node_id == "join" for row in rows)
                    assert all(
                        row.input == {"capture_status": "redacted"}
                        and row.output is None
                        and row.completed_at >= row.started_at
                        for row in rows
                        if row.status == "cancelled"
                    )
                    if case == "stream":
                        terminal = [
                            frame for frame in frames if frame.get("event") == "workflow_finished"
                        ]
                        assert len(terminal) == 1 and terminal[0]["data"]["status"] == "cancelled"
                    report["checks"].append(
                        case + "_http_cancel_interrupts_both_calls_and_preserves_safe_branch_times"
                    )
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            from celery_app.tasks import execute_workflow_task

            before_calls = (await client.get(fixture_base + "/fixture/state")).json()["calls"]
            duplicate_id = "parallel-owned-duplicate-" + uuid4().hex
            execute_workflow_task.apply_async(
                args=[completed_success[0], flows["success"], {"case": "FORGED"}, project],
                task_id=duplicate_id,
                queue=queue,
            )
            for _ in range(150):
                result = broker.get("celery-task-meta-" + duplicate_id)
                if result and json.loads(result).get("status") == "SUCCESS":
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("The same owned duplicate task did not settle")
            _, duplicate_rows = await wait_run(
                flows["success"], lambda row, nodes: row.status == "completed"
            )
            assert len(duplicate_rows) == completed_success[1]
            assert (await client.get(fixture_base + "/fixture/state")).json()[
                "calls"
            ] == before_calls
            report["checks"].append(
                "cross_tenant_cancel_denied_and_duplicate_worker_message_does_not_replay"
            )
            report["synthetic_http_trace"] = (
                await client.get(fixture_base + "/fixture/state")
            ).json()["calls"]
            assert all(
                row["returned_at"] or row["disconnected_at"]
                for row in report["synthetic_http_trace"]
            )
            report["checks"].append(
                "all_owned_http_calls_settled_and_no_join_for_failed_or_cancelled_groups"
            )
            report["verification_completed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        raise RuntimeError(
            "Parallel verification failed at "
            + report.get("stage", "setup")
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
                    await db.execute(text('DROP DATABASE "' + database + '" WITH (FORCE)'))
                    report["owned_database_removed"] = not bool(
                        await db.scalar(
                            text("SELECT 1 FROM pg_database WHERE datname=:name"),
                            {"name": database},
                        )
                    )
            except Exception:
                errors.append("owned_database_cleanup_failed")
        try:
            keys = [queue, "_kombu.binding." + queue]
            for log in directory.glob("worker.log"):
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
            value and value in log.read_text(encoding="utf-8", errors="replace")
            for log in directory.glob("*.log")
            for value in (private, url.password or "")
        )
        report["cleanup_errors"] = errors
        report["production_sources_unchanged"] = report["source_hashes"] == hashes()
        report["verification_completed"] = bool(
            report["verification_completed"]
            and not errors
            and report["owned_database_removed"]
            and report["owned_queue_removed"]
            and report["owned_processes_stopped"]
            and not report["logs_contain_test_secrets"]
            and report["production_sources_unchanged"]
        )
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        (ROOT / ".tmp/native-dev/workflow-parallel-control-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    assert report["verification_completed"]
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    assert parser.parse_args().run_local
    asyncio.run(main())

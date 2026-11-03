"""Rotate ephemeral receipt keys across real loopback Workflow processes."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from typing import BinaryIO, NotRequired, TypedDict
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(WORKFLOW_ROOT))
from app.config import settings  # noqa: E402
from app.database import AsyncSessionLocal  # noqa: E402
from app.models.execution import WorkflowExecution  # noqa: E402
from app.models.workflow import Workflow  # noqa: E402
from app.models.workflow_release import WorkflowRelease  # noqa: E402
from app.services.workflow_service import WorkflowService  # noqa: E402


class RotationReport(TypedDict):
    project_id: str
    started_at_utc: str
    verification_completed: bool
    checks: list[str]
    checked_at_utc: NotRequired[str]
    temporary_process_removed: NotRequired[bool]
    owned_fixture_removed: NotRequired[bool]
    logs_exclude_key_material: NotRequired[bool]
    cleanup_errors: NotRequired[list[str]]


class TemporaryWorkflow:
    def __init__(self, port: int, directory: Path, http_key: str):
        self.port = port
        self.directory = directory
        self.http_key = http_key
        self.process: subprocess.Popen[bytes] | None = None
        self.log_paths: list[Path] = []
        self.log_file: BinaryIO | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        process = self.process
        if process is not None:
            if process.poll() is None:
                if os.name == "nt":
                    stopped = subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        timeout=15,
                        check=False,
                    )
                    if stopped.returncode and process.poll() is None:
                        raise RuntimeError(
                            "Owned temporary process did not stop"
                        )
                else:
                    process.terminate()
            process.wait(timeout=15)
            self.process = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None

    async def start(self, keys: dict[str, str] | None, active: str | None):
        self.stop()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", self.port))
            self.port = probe.getsockname()[1]
        child_env = dict(os.environ)
        child_env["SECRET_KEY"] = self.http_key
        child_env["WORKFLOW_EVENT_RECEIPT_KEYS"] = (
            json.dumps(keys) if keys is not None else ""
        )
        child_env["WORKFLOW_EVENT_RECEIPT_ACTIVE_KEY_ID"] = active or ""
        log = self.directory / f"phase-{len(self.log_paths)}.log"
        self.log_paths.append(log)
        self.log_file = log.open("wb")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=WORKFLOW_ROOT,
            env=child_env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 60
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(
                        "Temporary Workflow exited during startup"
                    )
                try:
                    response = await client.get(self.url + "/health")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
            else:
                raise RuntimeError("Temporary Workflow readiness timed out")
            # Identify our keyed listener before creating any database fixture.
            path = self.url + f"/v1/workflows/{uuid4()}/event-test-context"
            query = {"project_id": str(uuid4())}
            wrong = await client.get(
                path,
                params=query,
                headers={
                    "X-Internal-API-Key": "not-the-ephemeral-instance-key",
                },
            )
            right = await client.get(
                path,
                params=query,
                headers={
                    "X-Internal-API-Key": self.http_key,
                },
            )
            # The invalid-keyring phase is deliberately unavailable after auth.
            if wrong.status_code != 403 or right.status_code not in {404, 503}:
                raise RuntimeError(
                    "Temporary listener identity could not be verified"
                )

    def check_logs(self, material: list[str]) -> bool:
        clean = True
        for path in self.log_paths:
            content = path.read_text(encoding="utf-8", errors="replace")
            redacted = content
            for value in material:
                redacted = redacted.replace(
                    value, "[verification key redacted]"
                )
            if redacted != content:
                clean = False
                path.write_text(redacted, encoding="utf-8")
        return clean


async def main(port: int) -> None:
    if not __debug__:
        raise RuntimeError("Verification requires enabled Python assertions")
    if make_url(settings.DATABASE_URL).host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("Only a loopback development database is permitted")
    if not 0 <= port <= 65535:
        raise RuntimeError("Invalid temporary listener port")
    project = str(uuid4())
    workflow_id = None
    workflow_name = "receipt-rotation-" + project
    report: RotationReport = {
        "project_id": project,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
    }
    directory = ROOT / ".tmp/native-dev" / workflow_name
    directory.mkdir(parents=True, exist_ok=False)
    http_key, first_key, second_key = [
        secrets.token_urlsafe(48) for _ in range(3)
    ]
    material = [http_key, first_key, second_key]
    server = TemporaryWorkflow(port, directory, http_key)
    all_keys = {"legacy": http_key, "r1": first_key, "r2": second_key}
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:

        async def request(method, path, expected=200, **kwargs):
            response = await client.request(
                method, server.url + path, **kwargs
            )
            if any(value in response.text for value in material):
                raise RuntimeError(
                    "HTTP response exposed verification key material"
                )
            if response.status_code != expected:
                raise RuntimeError(
                    f"{method} {path}: HTTP {response.status_code}, "
                    f"expected {expected}"
                )
            return response

        async def context():
            return (
                await request(
                    "GET",
                    f"/v1/workflows/{workflow_id}/event-test-context",
                    params={"project_id": project},
                    headers={"X-Internal-API-Key": http_key},
                )
            ).json()

        async def send(body: bytes, event_id: str, expected=202):
            stamp = str(int(time.time()))
            message = (
                stamp.encode()
                + b"\n"
                + event_id.encode()
                + b"\n"
                + str(workflow_id).encode()
                + b"\n"
                + body
            )
            digest = hmac.new(
                http_key.encode(), message, hashlib.sha256
            ).hexdigest()
            return await request(
                "POST",
                f"/v1/workflows/{workflow_id}/events",
                expected,
                content=body,
                headers={
                    "X-Workflow-Timestamp": stamp,
                    "X-Workflow-Event-ID": event_id,
                    "X-Workflow-Signature": "sha256=" + digest,
                    "Content-Type": "application/json",
                },
            )

        async def completed(execution: str, key_id: str | None):
            for _ in range(120):
                value = (
                    await request(
                        "GET",
                        f"/v1/workflows/executions/{execution}",
                        params={"project_id": project},
                    )
                ).json()
                if value["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.25)
            assert (
                value["status"] == "completed"
            ), "Real worker did not complete"
            async with AsyncSessionLocal() as db:
                run = await db.get(WorkflowExecution, execution)
                assert run is not None and run.project_id == project
                assert run.event_receipt_key_id == key_id
                assert run.input is None and run.invocation_input_hmac
                return run.invocation_input_hmac

        try:
            await server.start(None, None)
            created = (
                await request(
                    "POST",
                    "/v1/workflows/",
                    201,
                    params={"project_id": project},
                    json={
                        "name": workflow_name,
                        "tags": ["temporary-key-rotation"],
                        "nodes": [
                            {
                                "id": "event",
                                "type": "event",
                                "position": {"x": 0, "y": 0},
                                "data": {
                                    "type": "event",
                                    "label": "内部测试事件",
                                    "reference_key": "event",
                                    "event_type": "test.key.rotation",
                                },
                            },
                            {
                                "id": "answer",
                                "type": "answer",
                                "position": {"x": 300, "y": 0},
                                "data": {
                                    "type": "answer",
                                    "label": "返回结果",
                                    "reference_key": "answer",
                                    "output_type": "template",
                                    "output_template": (
                                        "已处理：{{event.data.marker}}"
                                    ),
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
                    },
                )
            ).json()
            workflow_id = created["id"]
            await request(
                "POST",
                f"/v1/workflows/{workflow_id}/publish",
                params={"project_id": project},
                json={
                    "expected_draft_version": created["version"],
                    "expected_publication_revision": 0,
                },
            )
            publication = await context()

            def body(marker: str) -> bytes:
                return json.dumps(
                    {
                        "project_id": project,
                        "event_type": publication["event_type"],
                        "expected_release_id": publication["release_id"],
                        "expected_release_digest": publication[
                            "release_digest"
                        ],
                        "data": {"marker": marker},
                    },
                    separators=(",", ":"),
                ).encode()

            legacy_body = body("legacy synthetic event")
            legacy = (await send(legacy_body, "legacy-event")).json()
            legacy_proof = await completed(legacy["execution_id"], None)
            report["checks"].append("legacy_run_completed_and_input_scrubbed")
            await server.start({"legacy": http_key, "r1": first_key}, "r1")
            duplicate = (await send(legacy_body, "legacy-event")).json()
            assert (
                duplicate["deduplicated"]
                and duplicate["execution_id"] == legacy["execution_id"]
            )
            report["checks"].append("configured_ring_preserves_legacy_receipt")
            first_body = body("r1 synthetic event")
            first = (await send(first_body, "r1-event")).json()
            first_proof = await completed(first["execution_id"], "r1")
            report["checks"].append("new_receipt_uses_r1")
            print(
                "Legacy import and first versioned receipt verified",
                flush=True,
            )

            await server.start(all_keys, "r2")
            for data, identifier, previous in (
                (legacy_body, "legacy-event", legacy),
                (first_body, "r1-event", first),
            ):
                duplicate = (await send(data, identifier)).json()
                assert (
                    duplicate["deduplicated"]
                    and duplicate["execution_id"] == previous["execution_id"]
                )
            report["checks"].append(
                "active_key_rotation_preserves_old_receipts"
            )
            second_body = body("r2 synthetic event")
            second = (await send(second_body, "r2-event")).json()
            await completed(second["execution_id"], "r2")
            report["checks"].append("new_receipt_uses_r2")

            await server.start({"r2": second_key}, "r2")
            await send(legacy_body, "legacy-event", 503)
            await send(first_body, "r1-event", 503)
            report["checks"].append("missing_old_keys_return_503")
            duplicate = (await send(second_body, "r2-event")).json()
            assert (
                duplicate["deduplicated"]
                and duplicate["execution_id"] == second["execution_id"]
            )
            await send(body("different content"), "r2-event", 409)
            report["checks"].append(
                "retained_key_deduplicates_and_detects_conflict"
            )
            print(
                "Missing historical keys reject without replacement",
                flush=True,
            )

            await server.start({"r2": second_key}, "absent")
            await request(
                "GET",
                f"/v1/workflows/{workflow_id}/event-test-context",
                503,
                params={"project_id": project},
                headers={"X-Internal-API-Key": http_key},
            )
            await send(
                body("invalid config new event"), "must-not-create", 503
            )
            report["checks"].append(
                "invalid_explicit_configuration_has_no_fallback"
            )

            await server.start(all_keys, "r2")
            for data, identifier, previous in (
                (legacy_body, "legacy-event", legacy),
                (first_body, "r1-event", first),
                (second_body, "r2-event", second),
            ):
                duplicate = (await send(data, identifier)).json()
                assert (
                    duplicate["deduplicated"]
                    and duplicate["execution_id"] == previous["execution_id"]
                )
            assert (
                await completed(legacy["execution_id"], None) == legacy_proof
            )
            assert await completed(first["execution_id"], "r1") == first_proof
            report["checks"].append(
                "restored_keys_reuse_original_proofs_and_runs"
            )
            async with AsyncSessionLocal() as db:
                count = await db.scalar(
                    select(func.count())
                    .select_from(WorkflowExecution)
                    .where(
                        WorkflowExecution.project_id == project,
                        WorkflowExecution.workflow_id == workflow_id,
                    )
                )
                assert (
                    count == 3
                ), "Rejected or repeated events created extra runs"
            report["checks"].append(
                "exactly_three_executions_after_all_retries"
            )
            report["verification_completed"] = True
        finally:
            cleanup_errors: list[str] = []
            if workflow_id:
                try:
                    async with AsyncSessionLocal() as db:
                        saved = await db.get(Workflow, workflow_id)
                        if saved is not None:
                            if (
                                saved.project_id != project
                                or saved.name != workflow_name
                            ):
                                raise RuntimeError(
                                    "Workflow fixture ownership mismatch"
                                )
                            await WorkflowService.delete(
                                db, workflow_id, project
                            )
                            await db.commit()
                        for model in (WorkflowExecution, WorkflowRelease):
                            count = await db.scalar(
                                select(func.count())
                                .select_from(model)
                                .where(model.project_id == project)
                            )
                            assert count == 0, "Owned workflow rows remain"
                        assert await db.get(Workflow, workflow_id) is None
                    report["owned_fixture_removed"] = True
                except Exception:
                    cleanup_errors.append("owned workflow fixture")
            try:
                server.stop()
                report["temporary_process_removed"] = True
            except Exception:
                cleanup_errors.append("owned temporary process")
            report["logs_exclude_key_material"] = server.check_logs(material)
            report["cleanup_errors"] = cleanup_errors
            report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
            if cleanup_errors or not report["logs_exclude_key_material"]:
                report["verification_completed"] = False
            target = (
                ROOT
                / ".tmp/native-dev/workflow-event-key-rotation-report.json"
            )
            target.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if cleanup_errors:
                raise RuntimeError(
                    "Owned rotation fixtures could not be cleaned"
                )
            if not report["logs_exclude_key_material"]:
                raise RuntimeError("Verification logs required key redaction")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=0)
    arguments = parser.parse_args()
    try:
        asyncio.run(main(arguments.port))
    except Exception as error:
        if isinstance(error, (RuntimeError, AssertionError)):
            print(f"{type(error).__name__}: {error}")
        else:
            print(type(error).__name__)
        sys.exit(1)

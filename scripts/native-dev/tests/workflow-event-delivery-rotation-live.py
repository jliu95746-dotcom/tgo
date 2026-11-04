"""Rotate frozen delivery keys across actual publisher processes."""

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy.engine import make_url
import uvicorn

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models import Project  # noqa: E402
from app.models.workflow_event_subscription import (  # noqa: E402
    WorkflowEventDelivery,
)  # noqa: E402,E501
from app.services.workflow_event_crypto import (  # noqa: E402
    encrypt_event_payload,
)  # noqa: E402,E501
from app.services.workflow_source_dispatch import (  # noqa: E402
    SourceEventClaim,
    deliver_source_event,
)
from app.services.wukongim_client import wukongim_client as im  # noqa: E402
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE  # noqa: E402
from workflow_delivery_rotation_fixture import CipherFixture  # noqa: E402


class CipherReport(TypedDict):
    project_id: str
    started_at_utc: str
    verification_completed: bool
    checks: list[str]
    checked_at_utc: NotRequired[str]
    publisher_pids: NotRequired[list[int]]
    owned_rows_removed: NotRequired[bool]
    owned_workflow_and_run_removed: NotRequired[bool]
    temporary_proxy_removed: NotRequired[bool]
    cleanup_errors: NotRequired[list[str]]


def validate_local() -> None:
    if not __debug__:
        raise RuntimeError("Verification requires enabled Python assertions")
    loopback = {"127.0.0.1", "localhost", "::1"}
    if make_url(settings.database_url_sync).host not in loopback:
        raise RuntimeError("Only a loopback development database is permitted")
    for value in (
        settings.API_BASE_URL,
        settings.WORKFLOW_SERVICE_URL,
        im.base_url,
    ):
        url = urlsplit(value)
        if (
            url.scheme != "http"
            or url.hostname not in loopback
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise RuntimeError(
                "Only credential-free loopback HTTP is permitted"
            )


async def publish(delivery_id: UUID, attempt: int) -> None:
    validate_local()
    project_id = UUID(os.environ["TGO_CIPHER_TEST_PROJECT"])
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        row = db.get(WorkflowEventDelivery, delivery_id)
        if (
            project is None
            or row is None
            or row.project_id != project_id
            or project.name != "cipher-delivery-live-" + project_id.hex
            or project.api_key != os.environ["TGO_CIPHER_TEST_OWNER_KEY"]
        ):
            raise RuntimeError("Publisher fixture ownership mismatch")
        await deliver_source_event(db, SourceEventClaim(delivery_id, attempt))
    # A separate new write must use the newly selected active key.
    fresh = encrypt_event_payload("synthetic fresh-write check")
    prefix = (
        "ev1:" + (settings.WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID or "") + ":"
    )
    assert fresh.startswith(prefix)
    print(
        json.dumps({"pid": os.getpid(), "fresh_write_uses_active_key": True})
    )


async def main() -> None:
    validate_local()
    fixture = CipherFixture()
    report: CipherReport = {
        "project_id": str(fixture.project_id),
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "checks": [],
    }
    directory = ROOT / ".tmp/native-dev"
    directory.mkdir(parents=True, exist_ok=True)
    keys = {"c1": secrets.token_urlsafe(48), "c2": secrets.token_urlsafe(48)}
    workflow_id = None
    subscription = None
    channel_attempted = False
    body_digests: list[str] = []
    event_ids: list[str] = []
    execution_ids: list[str] = []
    deduplicated: list[bool] = []
    publisher_pids: list[int] = []
    proxy_server = None
    proxy_task = None
    listener = None
    drop_acknowledgement = True
    async with httpx.AsyncClient(timeout=15, trust_env=False) as transport:
        proxy = FastAPI()

        @proxy.api_route("/{path:path}", methods=["GET", "POST"])
        async def forward(path: str, request: Request) -> Response:
            nonlocal drop_acknowledgement
            root = f"v1/workflows/{workflow_id}"
            if path not in {root + "/events", root + "/event-test-context"}:
                return Response(status_code=403)
            body = await request.body()
            if len(body) > 65536:
                return Response(status_code=413)
            if request.method == "GET":
                if request.query_params.get("project_id") != str(
                    fixture.project_id
                ):
                    return Response(status_code=403)
            else:
                data = json.loads(body)
                if data.get("project_id") != str(fixture.project_id):
                    return Response(status_code=403)
                if data.get("source_binding", {}).get("delivery_id") != str(
                    fixture.delivery_id
                ):
                    return Response(status_code=403)
            headers = {
                name: value
                for name, value in request.headers.items()
                if name
                in {
                    "content-type",
                    "x-internal-api-key",
                    "x-workflow-timestamp",
                    "x-workflow-event-id",
                    "x-workflow-signature",
                }
            }
            result = await transport.request(
                request.method,
                settings.WORKFLOW_SERVICE_URL.rstrip("/") + "/" + path,
                params=request.query_params,
                content=body,
                headers=headers,
            )
            if request.method == "POST" and result.status_code == 202:
                body_digests.append(hashlib.sha256(body).hexdigest())
                event_ids.append(request.headers["x-workflow-event-id"])
                execution_ids.append(result.json()["execution_id"])
                deduplicated.append(result.json()["deduplicated"])
                if drop_acknowledgement:
                    drop_acknowledgement = False
                    return Response(
                        content='{"detail":"synthetic_ack_lost"}',
                        status_code=504,
                        media_type="application/json",
                    )
            return Response(
                content=result.content,
                status_code=result.status_code,
                media_type="application/json",
            )

        async with httpx.AsyncClient(
            base_url=settings.API_BASE_URL,
            timeout=20,
            trust_env=False,
        ) as api:

            async def request(method, path, expected=200, **kwargs):
                response = await api.request(method, path, **kwargs)
                if response.status_code != expected:
                    raise RuntimeError(
                        f"{method} {path}: HTTP {response.status_code}"
                    )
                return response

            async def run_publisher(
                attempt: int, ring: dict[str, str], active: str
            ):
                child_env = dict(os.environ)
                child_env["NO_PROXY"] = "127.0.0.1,localhost,::1"
                child_env["no_proxy"] = child_env["NO_PROXY"]
                assert listener is not None
                child_env[
                    "WORKFLOW_SERVICE_URL"
                ] = f"http://127.0.0.1:{listener.getsockname()[1]}"
                child_env["WORKFLOW_EVENT_DELIVERY_KEYS"] = json.dumps(ring)
                child_env["WORKFLOW_EVENT_DELIVERY_ACTIVE_KEY_ID"] = active
                child_env["TGO_CIPHER_TEST_PROJECT"] = str(fixture.project_id)
                child_env[
                    "TGO_CIPHER_TEST_OWNER_KEY"
                ] = fixture.owner_key.get_secret_value()
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--run-local",
                        "--delivery-id",
                        str(fixture.delivery_id),
                        "--attempt",
                        str(attempt),
                    ],
                    env=child_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                )
                if result.returncode:
                    raise RuntimeError("Owned publisher subprocess failed")
                output = json.loads(result.stdout.strip().splitlines()[-1])
                assert output["fresh_write_uses_active_key"]
                publisher_pids.append(output["pid"])

            try:
                fixture.seed()
                api.headers["Authorization"] = "Bearer " + fixture.token()
                created = (
                    await request(
                        "POST",
                        "/v1/ai/workflows",
                        201,
                        json={
                            "name": fixture.name,
                            "tags": ["temporary-cipher-rotation"],
                            "nodes": [
                                {
                                    "id": "event",
                                    "type": "event",
                                    "position": {"x": 0, "y": 0},
                                    "data": {
                                        "type": "event",
                                        "label": "会话结束",
                                        "reference_key": "event",
                                        "event_type": "staff.session.closed",
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
                                            "{{event.data."
                                            "excerpts.0.content}}"
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
                    f"/v1/ai/workflows/{workflow_id}/publish",
                    json={
                        "expected_draft_version": created["version"],
                        "expected_publication_revision": 0,
                    },
                )
                context = (
                    await request(
                        "GET",
                        f"/v1/ai/workflows/{workflow_id}/event-test-context",
                    )
                ).json()
                subscription = (
                    await request(
                        "PUT",
                        f"/v1/ai/workflows/{workflow_id}/event-subscription",
                        json={
                            "enabled": True,
                            "expected_revision": 0,
                            "expected_release_id": context["release_id"],
                            "expected_release_digest": context[
                                "release_digest"
                            ],
                        },
                    )
                ).json()
                channel_attempted = True
                await im.create_channel(
                    channel_id=fixture.channel,
                    channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    subscribers=[fixture.staff_uid, fixture.channel],
                )
                await im.send_message(
                    from_uid=fixture.channel,
                    channel_id=fixture.channel,
                    channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    no_persist=False,
                    payload={
                        "type": 1,
                        "content": "模拟咨询女包，邮箱 cipher@example.test",
                    },
                )
                maximum = None
                for _ in range(20):
                    maximum = await im.get_channel_max_message_seq(
                        channel_id=fixture.channel,
                        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                        login_uid=fixture.staff_uid,
                    )
                    if maximum and maximum > 0:
                        break
                    await asyncio.sleep(0.1)
                assert maximum and maximum > 0
                await asyncio.sleep(1.1)
                fixture.stage(
                    workflow_id,
                    subscription["subscription_id"],
                    context["release_id"],
                    context["release_digest"],
                    subscription["revision"],
                    maximum,
                )
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen(16)
                proxy_server = uvicorn.Server(
                    uvicorn.Config(
                        proxy,
                        lifespan="off",
                        log_config=None,
                        access_log=False,
                    )
                )
                proxy_task = asyncio.create_task(
                    proxy_server.serve(sockets=[listener])
                )
                for _ in range(100):
                    if proxy_server.started:
                        break
                    if proxy_task.done():
                        raise RuntimeError("Owned proxy exited during startup")
                    await asyncio.sleep(0.1)
                assert proxy_server.started
                await run_publisher(1, {"c1": keys["c1"]}, "c1")
                first = fixture.state()
                assert (
                    first.status == "pending"
                    and first.error == "workflow_temporarily_unavailable"
                )
                assert first.ciphertext and first.ciphertext.startswith(
                    "ev1:c1:"
                )
                frozen = first.ciphertext
                fixture.park()
                assert len(execution_ids) == 1
                report["checks"].append(
                    "accepted_then_lost_ack_preserves_versioned_cipher"
                )
                print(
                    "Accepted event retained as a frozen pending delivery",
                    flush=True,
                )
                for _ in range(100):
                    async with httpx.AsyncClient(
                        base_url=settings.WORKFLOW_SERVICE_URL,
                        timeout=10,
                        trust_env=False,
                    ) as workflow:
                        result = await workflow.get(
                            f"/v1/workflows/executions/{execution_ids[0]}",
                            params={"project_id": str(fixture.project_id)},
                        )
                        result.raise_for_status()
                        executed = result.json()
                    if executed["status"] in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        break
                    await asyncio.sleep(0.25)
                assert (
                    executed["status"] == "completed"
                    and executed["input"] is None
                )
                answer = json.dumps(executed["output"], ensure_ascii=False)
                assert "[邮箱]" in answer and "cipher@example.test" not in answer
                report["checks"].append(
                    "real_worker_completed_redacted_source_before_retry"
                )
                fixture.claim(2)
                await run_publisher(2, {"c2": keys["c2"]}, "c2")
                missing = fixture.state()
                assert (
                    missing.status == "pending"
                    and missing.error == "event_delivery_key_unavailable"
                )
                assert missing.ciphertext == frozen and len(body_digests) == 1
                fixture.park()
                report["checks"].append(
                    "missing_old_cipher_key_retains_exact_cipher_without_post"
                )
                fixture.claim(3)
                await run_publisher(3, keys, "c2")
                restored = fixture.state()
                assert (
                    restored.status == "accepted"
                    and restored.ciphertext is None
                )
                assert restored.execution_id == execution_ids[0]
                assert len(body_digests) == 2 and len(set(body_digests)) == 1
                assert len(set(event_ids)) == len(set(execution_ids)) == 1
                assert deduplicated == [False, True]
                assert len(set(publisher_pids)) == 3
                runs_response = await transport.get(
                    settings.WORKFLOW_SERVICE_URL.rstrip("/")
                    + f"/v1/workflows/{workflow_id}/executions",
                    params={"project_id": str(fixture.project_id)},
                )
                runs_response.raise_for_status()
                runs = runs_response.json()
                assert len(runs) == 1 and runs[0]["id"] == execution_ids[0]
                report["checks"].extend(
                    [
                        "restored_old_key_reuses_identical_body_and_event_id",
                        "receiver_reuses_original_run_after_scrub",
                        "accepted_delivery_clears_ciphertext",
                        "distinct_publishers_load_key_configs",
                        "fresh_cipher_writes_use_selected_active_key",
                        "one_receiver_execution_and_a_deduplicated_retry",
                    ]
                )
                report["publisher_pids"] = publisher_pids
                report["verification_completed"] = True
            finally:
                errors: list[str] = []
                if proxy_server is not None:
                    proxy_server.should_exit = True
                if proxy_task is not None:
                    try:
                        await asyncio.wait_for(proxy_task, 10)
                        report["temporary_proxy_removed"] = True
                    except Exception:
                        errors.append("owned proxy")
                if listener is not None:
                    listener.close()
                if workflow_id:
                    cleanup_path = (
                        f"/v1/ai/workflows/{workflow_id}/event-subscription"
                    )
                    try:
                        if subscription:
                            fresh = (
                                await request(
                                    "GET",
                                    cleanup_path,
                                )
                            ).json()
                            await request(
                                "DELETE",
                                cleanup_path,
                                params={
                                    "expected_revision": fresh["revision"]
                                },
                            )
                        await request(
                            "DELETE", f"/v1/ai/workflows/{workflow_id}", 204
                        )
                        await request(
                            "GET", f"/v1/ai/workflows/{workflow_id}", 404
                        )
                        if execution_ids:
                            deleted = await transport.get(
                                settings.WORKFLOW_SERVICE_URL.rstrip("/")
                                + "/v1/workflows/executions/"
                                + execution_ids[0],
                                params={"project_id": str(fixture.project_id)},
                            )
                            assert deleted.status_code == 404
                        report["owned_workflow_and_run_removed"] = True
                    except Exception:
                        errors.append("owned workflow")
                if channel_attempted:
                    try:
                        for uid in (fixture.staff_uid, fixture.channel):
                            await im.delete_conversation(
                                uid=uid,
                                channel_id=fixture.channel,
                                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                            )
                        result = await transport.post(
                            im.base_url.rstrip("/") + "/channel/delete",
                            json={
                                "channel_id": fixture.channel,
                                "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE,
                            },
                        )
                        result.raise_for_status()
                    except Exception:
                        errors.append("owned IM channel")
                try:
                    fixture.cleanup()
                    report["owned_rows_removed"] = True
                except Exception:
                    errors.append("owned API rows")
                await im.aclose()
                report["cleanup_errors"] = errors
                report["checked_at_utc"] = datetime.now(
                    timezone.utc
                ).isoformat()
                if errors:
                    report["verification_completed"] = False
                (
                    directory / "workflow-event-delivery-rotation-report.json"
                ).write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if errors:
                    raise RuntimeError("Cipher fixture cleanup incomplete")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", required=True, action="store_true")
    parser.add_argument("--delivery-id", type=UUID)
    parser.add_argument("--attempt", type=int, default=1)
    options = parser.parse_args()
    try:
        if options.delivery_id:
            asyncio.run(publish(options.delivery_id, options.attempt))
        else:
            asyncio.run(main())
    except Exception as error:
        if isinstance(error, (RuntimeError, AssertionError)):
            print(f"{type(error).__name__}: {error}")
        else:
            print(type(error).__name__)
            import traceback

            for frame in traceback.extract_tb(error.__traceback__)[-3:]:
                print(
                    json.dumps(
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                    )
                )
        sys.exit(1)

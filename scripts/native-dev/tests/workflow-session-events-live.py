"""Verify an owned session-close source through local IM and a real worker."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import delete, inspect, select
from sqlalchemy.engine import make_url

logging.disable(logging.CRITICAL)
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))
from app.core.config import settings  # noqa: E402
from app.core.database import Base, SessionLocal  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402
from app.models import (  # noqa: E402
    Project,
    Staff,
    Platform,
    Visitor,
    VisitorSession,
    SessionCommandDelivery,
)
from app.models.company_account import CompanyAccount  # noqa: E402
from app.models.workflow_event_subscription import (  # noqa: E402
    WorkflowEventDelivery,
)  # noqa: E402,E501
from app.services.wukongim_client import wukongim_client as im  # noqa: E402
from app.utils.const import (  # noqa: E402
    CHANNEL_TYPE_CUSTOMER_SERVICE,
    CHANNEL_TYPE_PROJECT_STAFF,
)
from app.utils.encoding import (  # noqa: E402
    build_visitor_channel_id,
    build_project_staff_channel_id,
)


class SourceVerificationReport(TypedDict):
    project_id: str
    checks: list[str]
    no_model_or_external_channel_calls: bool
    verification_completed: bool
    started_at_utc: str
    checked_at_utc: NotRequired[str]
    delivery_id: NotRequired[str]
    execution_id: NotRequired[str]
    owned_workflow_and_run_removed: NotRequired[bool]
    owned_api_rows_removed: NotRequired[bool]
    owned_im_channel_removed: NotRequired[bool]
    cleanup_errors: NotRequired[list[str]]


async def main(*, verify_timer_management: bool = False):
    if not __debug__:
        raise RuntimeError("Verification requires enabled Python assertions")
    loopback = {"127.0.0.1", "localhost", "::1"}
    api_url = settings.API_BASE_URL.rstrip("/")
    workflow_url = settings.WORKFLOW_SERVICE_URL.rstrip("/")
    for url in (api_url, workflow_url, im.base_url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in loopback
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "Only credential-free loopback HTTP endpoints are permitted"
            )
    if make_url(settings.database_url_sync).host not in loopback:
        raise RuntimeError("Only a loopback development database is permitted")
    report_directory = ROOT / ".tmp/native-dev"
    report_directory.mkdir(parents=True, exist_ok=True)
    project_id, staff_id, visitor_id, session_id, platform_id = [
        uuid4() for _ in range(5)
    ]
    project_name = "session-event-live-" + project_id.hex
    project_key = secrets.token_urlsafe(32)
    username = "evt-" + uuid4().hex[:24] + "@example.test"
    password = secrets.token_urlsafe(32)
    channel_id = build_visitor_channel_id(visitor_id)
    staff_uid = str(staff_id) + "-staff"
    workflow_id = None
    execution_id = None
    subscription = None
    channel_created = False
    login_attempted = False
    report: SourceVerificationReport = {
        "project_id": str(project_id),
        "checks": [],
        "no_model_or_external_channel_calls": True,
        "verification_completed": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    def seed_fixture():
        with SessionLocal() as db:
            db.add(
                Project(id=project_id, name=project_name, api_key=project_key)
            )
            db.flush()
            db.add(
                Staff(
                    id=staff_id,
                    project_id=project_id,
                    username=username,
                    password_hash=get_password_hash(password),
                    role="admin",
                    account_enabled=True,
                    is_active=True,
                    service_paused=False,
                    token_version=1,
                    email_verified_at=datetime.now(timezone.utc),
                )
            )
            db.add(
                Platform(
                    id=platform_id,
                    project_id=project_id,
                    name="临时网页测试渠道",
                    type="website",
                    api_key=secrets.token_urlsafe(32),
                    is_active=True,
                    ai_mode="off",
                )
            )
            db.add(
                CompanyAccount(
                    project_id=project_id,
                    status="trial",
                    trial_granted=True,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    seat_limit=2,
                )
            )
            db.flush()
            db.add(
                Visitor(
                    id=visitor_id,
                    project_id=project_id,
                    platform_id=platform_id,
                    platform_open_id=uuid4().hex,
                    ai_disabled=True,
                    service_mode="manual",
                )
            )
            db.flush()
            db.add(
                VisitorSession(
                    id=session_id,
                    project_id=project_id,
                    visitor_id=visitor_id,
                    platform_id=platform_id,
                    staff_id=staff_id,
                    status="open",
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    - timedelta(minutes=1),
                )
            )
            db.commit()

    async with httpx.AsyncClient(
        base_url=api_url, timeout=20, trust_env=False
    ) as api:

        async def request(method, path, expected=200, **kwargs):
            response = await api.request(method, path, **kwargs)
            if response.status_code != expected:
                raise RuntimeError(
                    f"{method} {path}: HTTP {response.status_code}, "
                    f"expected {expected}"
                )
            return response

        try:
            seed_fixture()
            login_attempted = True
            login = await request(
                "POST",
                "/v1/staff/login",
                data={"username": username, "password": password},
            )
            token = login.json()["access_token"]
            api.headers["Authorization"] = "Bearer " + token
            report["checks"].append("temporary_staff_real_login")
            cli_env = dict(
                os.environ,
                TGO_TOKEN=token,
                TGO_SERVER=api_url,
                TGO_CONFIG_PATH=str(
                    report_directory / "unused-source-cli-config.json"
                ),
            )
            for command in (
                ["auth", "whoami"],
                ["workflow", "list", "--limit", "1"],
            ):
                result = await asyncio.to_thread(
                    subprocess.run,
                    [
                        "node",
                        str(ROOT / "repos/tgo-cli/dist/index.js"),
                        "--server",
                        api_url,
                        *command,
                    ],
                    env=cli_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=20,
                )
                if result.returncode:
                    raise RuntimeError(
                        "Synthetic staff CLI failed: " + " ".join(command)
                    )
            report["checks"].append(
                "synthetic_staff_cli_auth_and_workflow_list"
            )
            created = (
                await request(
                    "POST",
                    "/v1/ai/workflows",
                    201,
                    json={
                        "name": "会话结束来源临时样板",
                        "description": "Owned local source verification",
                        "tags": ["temporary-source-verification"],
                        "nodes": [
                            {
                                "id": "event",
                                "type": "event",
                                "position": {"x": 0, "y": 0},
                                "data": {
                                    "type": "event",
                                    "label": "客服会话结束",
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
                                    "label": "核对片段",
                                    "reference_key": "answer",
                                    "output_type": "template",
                                    "output_template": (
                                        "收到片段："
                                        "{{event.data.excerpts.0.content}}"
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
            if verify_timer_management:
                schedule_path = f"/v1/ai/workflows/{workflow_id}/schedule"
                state = (await request("GET", schedule_path)).json()
                assert state["runtime"]["status"] == "disabled"
                assert not state["runtime"]["ready"]
                assert not state["execution_available"]
                assert state["schedule"] is None
                report["checks"].append(
                    "native_gateway_timer_state_default_disabled"
                )
                preview_request = {
                    "cron_expression": "0 9 * * 1-5",
                    "timezone": "Asia/Shanghai",
                }
                preview = (await request(
                    "POST", schedule_path + "/preview", json=preview_request,
                )).json()
                assert len(preview["preview_at"]) == 5
                assert len(set(preview["preview_at"])) == 5
                assert preview["timezone"] == "Asia/Shanghai"
                await request(
                    "POST", schedule_path + "/preview", 422,
                    json={**preview_request, "actor_staff_id": str(staff_id)},
                )
                report["checks"].append(
                    "native_gateway_timer_preview_and_identity_rejection"
                )
                history = (await request(
                    "GET", schedule_path + "/occurrences",
                )).json()
                assert history["items"] == [] and history["total"] == 0
                unchanged = (await request("GET", schedule_path)).json()
                assert unchanged["schedule"] is None
                report["checks"].append(
                    "timer_reads_and_preview_do_not_register_work"
                )
                web_port = int(os.environ.get("TGO_WEB_PORT", "5173"))
                assert 1 <= web_port <= 65535
                async with httpx.AsyncClient(
                    timeout=10, trust_env=False,
                ) as web:
                    response = await web.get(
                        f"http://127.0.0.1:{web_port}/api" + schedule_path,
                        headers={"Authorization": "Bearer " + token},
                    )
                assert response.status_code == 200
                proxied = response.json()
                assert proxied["schedule"] is None
                assert proxied["runtime"]["status"] == "disabled"
                assert not proxied["runtime"]["ready"]
                assert not proxied["execution_available"]
                assert response.headers["cache-control"] == "no-store"
                report["checks"].append(
                    "actual_vite_proxy_delivers_timer_runtime_state"
                )
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
                    "GET", f"/v1/ai/workflows/{workflow_id}/event-test-context"
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
                        "expected_release_digest": context["release_digest"],
                    },
                )
            ).json()
            assert subscription["enabled"]
            report["checks"].append(
                "authenticated_publication_and_source_opt_in"
            )
            print("Authenticated source subscription created", flush=True)
            # Cleanup also owns a create whose acknowledgement is uncertain.
            channel_created = True
            await im.create_channel(
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                subscribers=[staff_uid, channel_id],
            )
            sent = await im.send_message(
                from_uid=channel_id,
                channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                payload={
                    "type": 1,
                    "content": "模拟客户咨询女包材质，邮箱 qa@example.test",
                },
                client_msg_no="synthetic-" + uuid4().hex,
                no_persist=False,
            )
            assert sent is not None
            for _ in range(20):
                maximum = await im.get_channel_max_message_seq(
                    channel_id=channel_id,
                    channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    login_uid=staff_uid,
                )
                if maximum and maximum > 0:
                    break
                await asyncio.sleep(0.1)
            assert (
                maximum and maximum > 0
            ), "Synthetic message not persisted in local IM"
            await asyncio.sleep(1.1)
            operation = uuid4()
            closed = await request(
                "POST",
                f"/v1/sessions/{session_id}/close",
                headers={"X-Session-Operation-ID": str(operation)},
            )
            assert closed.json()["status"] == "closed"
            with SessionLocal() as db:
                source = db.get(SessionCommandDelivery, operation)
                if source is None:
                    source = db.scalar(
                        select(SessionCommandDelivery).where(
                            SessionCommandDelivery.project_id == project_id,
                            SessionCommandDelivery.session_id == session_id,
                        )
                    )
                assert source is not None and source.kind == "close"
                source_id = source.id
            report["checks"].append(
                "authenticated_close_committed_source_fact"
            )
            print(
                "Close committed; waiting for the source recovery loop",
                flush=True,
            )
            accepted = None
            for _ in range(100):
                rows = (
                    await request(
                        "GET",
                        f"/v1/ai/workflows/{workflow_id}/event-deliveries",
                    )
                ).json()["items"]
                if rows:
                    accepted = rows[0]
                    if accepted["status"] in {
                        "accepted",
                        "failed",
                        "cancelled",
                    }:
                        break
                await asyncio.sleep(0.5)
            if not accepted or accepted["status"] != "accepted":
                code = (
                    accepted.get("last_error") if accepted else "no_delivery"
                )
                raise RuntimeError(
                    "Source dispatch did not accept: " + str(code)
                )
            execution_id = accepted["execution_id"]
            report["delivery_id"] = accepted["id"]
            report["execution_id"] = execution_id
            with SessionLocal() as db:
                delivery = db.get(WorkflowEventDelivery, accepted["id"])
                assert (
                    delivery.source_command_id == source_id
                    and delivery.encrypted_payload is None
                )
            report["checks"].append(
                "automatic_source_dispatch_accepted_and_ciphertext_cleared"
            )
            async with httpx.AsyncClient(
                base_url=workflow_url, timeout=10, trust_env=False
            ) as workflow:
                for _ in range(80):
                    response = await workflow.get(
                        f"/v1/workflows/executions/{execution_id}",
                        params={"project_id": str(project_id)},
                    )
                    response.raise_for_status()
                    current = response.json()
                    if current["status"] in {
                        "completed",
                        "failed",
                        "cancelled",
                    }:
                        break
                    await asyncio.sleep(0.25)
            assert (
                current["status"] == "completed"
            ), "Source-bound execution did not complete"
            output = json.dumps(current["output"], ensure_ascii=False)
            assert (
                "模拟客户咨询女包材质" in output
                and "[邮箱]" in output
                and "qa@example.test" not in output
            )
            assert (
                current["visitor_session_id"] == str(session_id)
                and current["input"] is None
            )
            report["checks"].extend(
                [
                    "real_im_excerpt_redacted_and_used_by_worker",
                    "source_bound_terminal_input_cleared",
                ]
            )
            with SessionLocal() as db:
                rows = db.scalars(
                    select(WorkflowEventDelivery).where(
                        WorkflowEventDelivery.project_id == project_id
                    )
                ).all()
                assert (
                    len(rows) == 1 and rows[0].source_command_id == source_id
                )
            report["checks"].append("one_delivery_for_committed_close_fact")
            disabled = (
                await request(
                    "DELETE",
                    f"/v1/ai/workflows/{workflow_id}/event-subscription",
                    params={"expected_revision": subscription["revision"]},
                )
            ).json()
            assert not disabled["enabled"]
            subscription = disabled
            report["checks"].append("authenticated_source_disable")
            report["verification_completed"] = True
        finally:
            cleanup_errors = []
            with SessionLocal() as db:
                owner = db.get(Project, project_id)
                if owner is not None and (
                    owner.name != project_name or owner.api_key != project_key
                ):
                    raise RuntimeError(
                        "Fixture ownership mismatch; no cleanup was attempted"
                    )
            if workflow_id:
                cleanup_path = (
                    f"/v1/ai/workflows/{workflow_id}/event-subscription"
                )
                try:
                    if subscription and subscription["enabled"]:
                        fresh = (
                            await request(
                                "GET",
                                cleanup_path,
                            )
                        ).json()
                        await request(
                            "DELETE",
                            cleanup_path,
                            params={"expected_revision": fresh["revision"]},
                        )
                    await request(
                        "DELETE", f"/v1/ai/workflows/{workflow_id}", 204
                    )
                    await request(
                        "GET", f"/v1/ai/workflows/{workflow_id}", 404
                    )
                    if execution_id:
                        async with httpx.AsyncClient(
                            base_url=workflow_url,
                            timeout=10,
                            trust_env=False,
                        ) as workflow:
                            deleted_run = await workflow.get(
                                "/v1/workflows/executions/" + execution_id,
                                params={"project_id": str(project_id)},
                            )
                            if deleted_run.status_code != 404:
                                raise RuntimeError("Owned run still exists")
                    report["owned_workflow_and_run_removed"] = True
                except Exception:
                    cleanup_errors.append("owned workflow")
            if channel_created:
                for uid in (staff_uid, channel_id):
                    try:
                        await im.delete_conversation(
                            uid=uid,
                            channel_id=channel_id,
                            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                        )
                    except Exception:
                        cleanup_errors.append("owned IM conversation")
                async with httpx.AsyncClient(
                    base_url=im.base_url, timeout=10, trust_env=False
                ) as channel:
                    response = await channel.post(
                        "/channel/delete",
                        json={
                            "channel_id": channel_id,
                            "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE,
                        },
                    )
                    if not response.is_success:
                        cleanup_errors.append("owned IM channel")
            staff_channel = build_project_staff_channel_id(project_id)
            if login_attempted:
                try:
                    await im.delete_conversation(
                        uid=staff_uid,
                        channel_id=staff_channel,
                        channel_type=CHANNEL_TYPE_PROJECT_STAFF,
                    )
                    async with httpx.AsyncClient(
                        base_url=im.base_url, timeout=10, trust_env=False
                    ) as channel:
                        response = await channel.post(
                            "/channel/delete",
                            json={
                                "channel_id": staff_channel,
                                "channel_type": CHANNEL_TYPE_PROJECT_STAFF,
                            },
                        )
                        if not response.is_success:
                            cleanup_errors.append("owned IM staff channel")
                except Exception:
                    cleanup_errors.append("owned IM staff channel")
            try:
                with SessionLocal() as db:
                    table_names = set(inspect(db.get_bind()).get_table_names())
                    for table in reversed(Base.metadata.sorted_tables):
                        if (
                            table.name in table_names
                            and table.name.startswith("api_")
                            and "project_id" in table.c
                        ):
                            db.execute(
                                delete(table).where(
                                    table.c.project_id == project_id
                                )
                            )
                    db.execute(delete(Project).where(Project.id == project_id))
                    db.commit()
                    assert db.get(Project, project_id) is None
                report["owned_api_rows_removed"] = True
            except Exception:
                cleanup_errors.append("owned API fixtures")
            await im.aclose()
            report["cleanup_errors"] = cleanup_errors
            report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
            report["owned_im_channel_removed"] = channel_created and not any(
                "IM" in item for item in cleanup_errors
            )
            (
                report_directory / "workflow-session-events-report.json"
            ).write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if cleanup_errors:
                raise RuntimeError(
                    "Cleanup incomplete: " + ", ".join(cleanup_errors)
                )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:] not in (
        ["--run-local"], ["--run-local", "--verify-timer-management"],
    ):
        raise SystemExit(
            "Use the native verification wrapper; --run-local is required."
        )
    try:
        asyncio.run(main(
            verify_timer_management="--verify-timer-management" in sys.argv,
        ))
    except Exception as error:
        if isinstance(error, (RuntimeError, AssertionError)):
            print(f"{type(error).__name__}: {error}")
        else:
            print(type(error).__name__)
        sys.exit(1)

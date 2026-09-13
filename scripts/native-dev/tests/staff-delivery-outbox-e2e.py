"""Local PostgreSQL + real platform callback smoke test, never a real customer.

Own API fixtures are deleted and the own platform-service fixture is soft-deleted.
The optional IM probe is non-persistent and uses an unbound random channel.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import delete
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos/tgo-api"))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models import ChannelMember, Platform, Project, Staff, Visitor  # noqa: E402
from app.models.staff_message_delivery import (
    StaffMessageDelivery,
    utc_now,
)  # noqa: E402
from app.schemas.staff_delivery import (
    AssistTrainingIntent,
    StaffDeliveryRequest,
)  # noqa: E402
from app.services import staff_delivery  # noqa: E402
from app.services.ai_client import ai_client  # noqa: E402
from app.services.platform_sync_client import platform_sync_client  # noqa: E402
from app.services.staff_message_target import resolve_staff_message_target  # noqa: E402
from app.services.wukongim_client import wukongim_client  # noqa: E402


def report(message: str) -> None:
    print(message, flush=True)


async def recover_test_project(project_id: UUID) -> None:
    with SessionLocal() as db:
        project = db.get(Project, project_id)
        if project is None or not project.name.startswith("staff-outbox-e2e-"):
            raise RuntimeError("Not an owned test project")
        history = AsyncMock()
        with patch.object(staff_delivery, "write_delivery_history", history):
            recovered = await staff_delivery.recover_pending_history(
                db, project_id=project_id
            )
        assert recovered == 1 and history.await_count == 1
        report("PASS fresh process recovers only the owned accepted history intent")


async def verify_training_receipt(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    project_id: UUID,
    visitor_id: UUID,
    skill_names: list[str],
    request: StaffDeliveryRequest,
    received: list[dict[str, object]],
) -> None:
    # Only this fixture changes mode/selection; the delivered correction must
    # keep its original target even if the worker completes after this change.
    with SessionLocal() as db:
        visitor = db.get(Visitor, visitor_id)
        assert visitor is not None and visitor.project_id == project_id
        visitor.service_mode = "manual"
        visitor.humanization_skill_name = skill_names[1]
        db.commit()
    params = {
        "channel_id": request.channel_id,
        "channel_type": 251,
        "client_msg_no": request.client_msg_no,
    }
    for _ in range(30):
        response = await client.get(
            "/v1/chat/messages/delivery", headers=headers, params=params
        )
        assert response.status_code == 200
        status = response.json()
        if status["training_status"] == "saved":
            break
        assert status["training_status"] == "pending", status["training_status"]
        await asyncio.sleep(0.5)
    else:
        raise AssertionError(
            "Background correction capture did not finish in 15 seconds"
        )
    assert status["training_skill_name"] == skill_names[0]
    review = await ai_client.review_humanization_training(
        str(project_id), skill_names[0]
    )
    assert review.published_version == 1 and not review.published
    assert len(review.pending) == 1
    sample = review.pending[0]
    assert sample.final_reply == request.payload["content"]
    assert sample.source_message_id == request.training.source_message_id
    other = await ai_client.review_humanization_training(
        str(project_id), skill_names[1]
    )
    assert not other.pending and other.published_version == 1
    report(
        "PASS live background worker captured the original skill once; selection changes do not retarget; no automatic publish"
    )

    # Reproduce a lost capture receipt after AI has already stored the sample.
    # Reusing the persisted delivery id must be idempotent in the running AI.
    with SessionLocal() as db:
        row = (
            db.query(StaffMessageDelivery)
            .filter(
                StaffMessageDelivery.project_id == project_id,
                StaffMessageDelivery.client_msg_no == request.client_msg_no,
            )
            .one()
        )
        row.training_status = "pending"
        row.training_next_retry_at = utc_now() - timedelta(seconds=1)
        db.commit()
    for _ in range(30):
        response = await client.get(
            "/v1/chat/messages/delivery", headers=headers, params=params
        )
        assert response.status_code == 200
        if response.json()["training_status"] == "saved":
            break
        await asyncio.sleep(0.5)
    else:
        raise AssertionError("Idempotent capture retry did not finish")
    review = await ai_client.review_humanization_training(
        str(project_id), skill_names[0]
    )
    assert len(review.pending) == 1 and review.published_version == 1
    repeated = await client.post(
        "/v1/chat/messages/deliver", headers=headers, json=request.model_dump()
    )
    assert repeated.status_code == 200 and repeated.json()["training_status"] == "saved"
    assert len(received) == 1
    restored = await client.get(
        "/v1/chat/messages/deliveries",
        headers=headers,
        params={"channel_id": request.channel_id, "channel_type": 251},
    )
    assert restored.status_code == 200 and restored.json() == []
    report(
        "PASS lost training receipt retries without duplicate samples or customer redelivery; settled records leave recovery list"
    )


async def verify(
    probe_im: bool, probe_api: bool = False, probe_training: bool = False
) -> None:
    local = {"127.0.0.1", "localhost", "::1"}
    if make_url(settings.database_url_sync).host not in local:
        raise RuntimeError("Only a local database is allowed")
    if any(
        urlsplit(url).hostname not in local
        for url in [
            platform_sync_client.base_url,
            wukongim_client.base_url,
            settings.API_BASE_URL,
        ]
    ):
        raise RuntimeError("Only local platform and IM services are allowed")
    own_project, own_platform, own_staff, own_visitor, own_member = [
        uuid4() for _ in range(5)
    ]
    fixture_name = f"staff-outbox-e2e-{uuid4().hex[:12]}"
    api_key = uuid4().hex
    channel_id = f"{own_visitor}-vtr"
    received: list[dict[str, object]] = []
    callback_path = f"/{uuid4().hex}"

    class Callback(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            if self.path != callback_path:
                self.send_error(404)
                return
            received.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "11")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_: object) -> None:
            pass

    callback = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
    thread = threading.Thread(target=callback.serve_forever, daemon=True)
    thread.start()
    created_platform = False
    created_skills: list[str] = []
    skill_names = [f"{fixture_name}-a", f"{fixture_name}-b"]
    try:
        with SessionLocal() as db:
            db.add(Project(id=own_project, name=fixture_name, api_key=uuid4().hex))
            db.flush()
            db.add_all(
                [
                    Staff(
                        id=own_staff,
                        project_id=own_project,
                        username=fixture_name,
                        password_hash="!disabled-isolated-test",
                        is_active=False,
                        role="admin",
                    ),
                    Platform(
                        id=own_platform,
                        project_id=own_project,
                        type="custom",
                        name=fixture_name,
                        api_key=api_key,
                        is_active=True,
                        ai_mode="off",
                        sync_status="synced",
                    ),
                ]
            )
            db.flush()
            db.add(
                Visitor(
                    id=own_visitor,
                    project_id=own_project,
                    platform_id=own_platform,
                    platform_open_id="isolated-recipient",
                    name=fixture_name,
                    service_mode="assist" if probe_training else "manual",
                    humanization_skill_name=skill_names[0] if probe_training else None,
                    humanization_skill_enabled=probe_training,
                )
            )
            db.add(
                ChannelMember(
                    id=own_member,
                    project_id=own_project,
                    channel_id=channel_id,
                    channel_type=251,
                    member_id=own_staff,
                    member_type="staff",
                )
            )
            db.commit()
        response = await platform_sync_client.upsert_platform(
            {
                "id": own_platform,
                "project_id": own_project,
                "name": fixture_name,
                "type": "custom",
                "api_key": api_key,
                "is_active": True,
                "config": {
                    "callback_url": f"http://127.0.0.1:{callback.server_port}{callback_path}"
                },
            }
        )
        response.raise_for_status()
        created_platform = True
        if probe_training:
            for skill_name in skill_names:
                detail = await ai_client.create_humanization_skill(
                    str(own_project),
                    {
                        "name": skill_name,
                        "display_name": "发送训练隔离验证",
                        "description": fixture_name,
                    },
                )
                assert detail["name"] == skill_name and detail["enabled"] is False
                created_skills.append(skill_name)
        request = StaffDeliveryRequest(
            channel_id=channel_id,
            client_msg_no=f"outbox-{uuid4().hex}",
            payload={
                "type": 1,
                "content": "isolated delivery test",
                "platform_open_id": "isolated-recipient",
            },
            training=AssistTrainingIntent(
                skill_name=skill_names[0],
                customer_message="有绿色吗？",
                ai_draft="抱歉，没有绿色。",
                source_message_id=f"{fixture_name}-question",
            )
            if probe_training
            else None,
        )
        if probe_training:
            request.payload["content"] = "没有绿色。"
        if probe_api:
            # Short-lived token belongs only to the randomly named test account.
            token = create_access_token(
                subject=fixture_name,
                project_id=own_project,
                role="admin",
                expires_delta=timedelta(minutes=2),
            )
            async with httpx.AsyncClient(
                base_url=settings.API_BASE_URL,
                timeout=30,
                trust_env=False,
            ) as client:
                denied = await client.post(
                    "/v1/chat/messages/deliver", json=request.model_dump()
                )
                assert denied.status_code in (
                    401,
                    403,
                ), f"Unauthenticated status: {denied.status_code}"
                assert not received
                headers = {"Authorization": f"Bearer {token}"}
                unauthorized = request.model_copy(
                    update={"channel_id": f"{uuid4()}-vtr"}
                )
                denied = await client.post(
                    "/v1/chat/messages/deliver",
                    headers=headers,
                    json=unauthorized.model_dump(),
                )
                assert denied.status_code == 403 and not received
                report(
                    "PASS live API rejects unauthenticated and unassigned-channel sends"
                )
                for _ in range(2):
                    sent = await client.post(
                        "/v1/chat/messages/deliver",
                        headers=headers,
                        json=request.model_dump(),
                    )
                    assert (
                        sent.status_code == 200
                    ), f"Delivery HTTP status: {sent.status_code}"
                    assert sent.json()["delivery_status"] == "sent"
                status = await client.get(
                    "/v1/chat/messages/delivery",
                    headers=headers,
                    params={
                        "channel_id": channel_id,
                        "channel_type": 251,
                        "client_msg_no": request.client_msg_no,
                    },
                )
                assert (
                    status.status_code == 200
                    and status.json()["delivery_status"] == "sent"
                )
                assert status.json()["history_status"] == "sent"
                assert len(received) == 1
                if probe_training:
                    await verify_training_receipt(
                        client,
                        headers,
                        own_project,
                        own_visitor,
                        skill_names,
                        request,
                        received,
                    )
                    report(
                        "NOTE IM retains an isolated synthetic test message; no customer data or recipients"
                    )
                    return
                # Simulate an unsettled receipt belonging only to this fixture.
                with SessionLocal() as db:
                    own_row = (
                        db.query(StaffMessageDelivery)
                        .filter(
                            StaffMessageDelivery.project_id == own_project,
                            StaffMessageDelivery.client_msg_no == request.client_msg_no,
                        )
                        .one()
                    )
                    own_row.external_status = "unknown"
                    own_row.history_status = "pending"
                    db.commit()
                restored = await client.get(
                    "/v1/chat/messages/deliveries",
                    headers=headers,
                    params={
                        "channel_id": channel_id,
                        "channel_type": 251,
                    },
                )
                assert restored.status_code == 200 and len(restored.json()) == 1
                assert restored.json()[0]["receipt"]["delivery_status"] == "unknown"
                assert restored.json()[0]["request"]["payload"] == request.payload
                assert len(received) == 1
                report(
                    "PASS live pending-receipt restore returns the original message without a resend"
                )
                report(
                    "PASS live authenticated HTTP delivery + real IM history; duplicate request delivered externally once"
                )
                report(
                    "NOTE IM retains an isolated synthetic test message; no customer data or recipients"
                )
            return
        with SessionLocal() as db:
            staff = db.get(Staff, own_staff)
            assert staff is not None
            target = resolve_staff_message_target(db, staff, channel_id, 251)
            with patch.object(
                staff_delivery,
                "write_delivery_history",
                AsyncMock(side_effect=RuntimeError("simulated IM outage")),
            ):
                first = await staff_delivery.deliver(db, target, request)
                again = await staff_delivery.deliver(db, target, request)
            assert first.delivery_status == again.delivery_status == "sent"
            assert first.history_status == "pending" and len(received) == 1
            row = staff_delivery.get_delivery(db, target, request.client_msg_no)
            row.next_retry_at = utc_now() - timedelta(seconds=1)
            db.commit()
        report(
            "PASS real channel HTTP delivery once; failed history remains in PostgreSQL"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            __file__,
            "--recover-project",
            str(own_project),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-2500:])
        report(stdout.decode(errors="replace").strip())
        with SessionLocal() as db:
            result = staff_delivery.receipt(
                staff_delivery.get_delivery(db, target, request.client_msg_no)
            )
            assert result.history_status == "sent" and len(received) == 1
        report(
            "PASS new process repaired history without calling the customer channel again"
        )
        if probe_im:
            probe_response = await wukongim_client.send_message(
                payload={
                    "type": 1,
                    "content": "isolated non-persistent transport probe",
                },
                from_uid=f"{own_staff}-staff",
                channel_id=f"{uuid4()}-vtr",
                channel_type=251,
                client_msg_no=f"probe-{uuid4().hex}",
                no_persist=True,
                red_dot=False,
            )
            assert (
                probe_response is not None
            ), "The actual IM HTTP receipt was not decoded"
            report(
                "PASS actual IM transport receipt (non-persistent, no customer subscribers)"
            )
    finally:
        for skill_name in created_skills:
            detail = await ai_client.get_skill(str(own_project), skill_name)
            assert detail["description"] == fixture_name and skill_name in skill_names
            await ai_client.delete_skill(str(own_project), skill_name)
        if created_skills:
            report("CLEANED only the two owned temporary training skills and samples")
        if created_platform:
            response = await platform_sync_client.delete_platform(str(own_platform))
            if response is None or response.status_code != 204:
                raise RuntimeError(f"Own platform cleanup failed: {own_platform}")
        with SessionLocal() as db:
            project = db.get(Project, own_project)
            if project is not None:
                if project.name != fixture_name:
                    raise RuntimeError(
                        "Project ownership changed; preserving instead of deleting"
                    )
                db.execute(
                    delete(StaffMessageDelivery).where(
                        StaffMessageDelivery.project_id == own_project
                    )
                )
                db.execute(delete(ChannelMember).where(ChannelMember.id == own_member))
                db.execute(
                    delete(Visitor).where(
                        Visitor.id == own_visitor, Visitor.project_id == own_project
                    )
                )
                db.execute(
                    delete(Staff).where(
                        Staff.id == own_staff, Staff.project_id == own_project
                    )
                )
                db.execute(
                    delete(Platform).where(
                        Platform.id == own_platform, Platform.project_id == own_project
                    )
                )
                db.execute(
                    delete(Project).where(
                        Project.id == own_project, Project.name == fixture_name
                    )
                )
                db.commit()
        callback.shutdown()
        callback.server_close()
        thread.join(timeout=5)
        report("CLEANED or rolled back own API fixtures")
        if created_platform:
            report("CLEANED own platform-service fixture (disabled/soft-deleted)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-im", action="store_true")
    parser.add_argument("--probe-api", action="store_true")
    parser.add_argument("--probe-training", action="store_true")
    parser.add_argument("--recover-project", type=UUID)
    args = parser.parse_args()
    asyncio.run(
        recover_test_project(args.recover_project)
        if args.recover_project
        else verify(
            args.probe_im, args.probe_api or args.probe_training, args.probe_training
        )
    )

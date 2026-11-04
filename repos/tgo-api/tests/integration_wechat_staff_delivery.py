"""Run ONLY in a new disposable PostgreSQL database, with no public network.

WECHAT_STAFF_ISOLATED_TEST=1 python tests/integration_wechat_staff_delivery.py
Uses DATABASE_URL through app settings. IM is mocked; foreign keys stay enabled.
"""

import asyncio
import importlib.util
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import Base
from app.models import (
    ChannelMember,
    Platform,
    Project,
    Staff,
    Visitor,
    VisitorAssignmentHistory,
    VisitorSession,
)
from app.models.staff_message_delivery import StaffMessageDelivery, utc_now
from app.models.customer_turn import CustomerTurn
from app.models.wechat_staff_delivery import WeChatStaffDelivery
from app.schemas.staff_delivery import StaffDeliveryRequest
from app.services import staff_delivery, wechat_staff_delivery as service
from app.services.staff_message_target import StaffMessageTarget


def run() -> None:
    if os.environ.get("WECHAT_STAFF_ISOLATED_TEST") != "1":
        raise RuntimeError("Explicit disposable-database opt in required")
    engine = create_engine(str(settings.DATABASE_URL), connect_args={"connect_timeout": 5})
    if engine.dialect.name != "postgresql" or inspect(engine).get_table_names():
        raise RuntimeError(
            "Requires a NEW EMPTY PostgreSQL database; refusing existing data"
        )
    settings.SAAS_ENABLED = False
    settings.SAAS_BILLING_ENABLED = False
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    # Real parent tables and foreign keys, not a relaxed mock schema.
    Base.metadata.create_all(
        engine,
        tables=[
            table
            for table in Base.metadata.sorted_tables
            if table.name != WeChatStaffDelivery.__tablename__
        ],
    )
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0048_wechat_staff_delivery.py"
    )
    spec = importlib.util.spec_from_file_location("wechat_delivery_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def migrate(direction: str) -> None:
        with engine.begin() as conn:
            context = MigrationContext.configure(conn)
            with Operations.context(context):
                getattr(migration, direction)()

    for direction in ["upgrade", "downgrade", "upgrade"]:
        migrate(direction)
    print("PASS migration upgrade/downgrade/upgrade")

    project_id, platform_id, staff_id, next_staff_id, visitor_id, session_id = [
        uuid4() for _ in range(6)
    ]
    with Session(engine) as db:
        db.add(
            Project(id=project_id, name="Isolated handoff", api_key="test-only-project")
        )
        db.flush()
        db.add_all(
            [
                Platform(
                    id=platform_id,
                    project_id=project_id,
                    name="Original bridge",
                    type="wechat_local",
                    api_key="test-only-platform",
                ),
                Staff(
                    id=staff_id,
                    project_id=project_id,
                    username="handoff-b",
                    password_hash="not-a-real-login",
                    role="admin",
                    is_active=True,
                    account_enabled=True,
                ),
                Staff(
                    id=next_staff_id,
                    project_id=project_id,
                    username="handoff-c",
                    password_hash="not-a-real-login",
                    role="admin",
                    is_active=True,
                    account_enabled=True,
                ),
            ]
        )
        db.flush()
        db.add(
            Visitor(
                id=visitor_id,
                project_id=project_id,
                platform_id=platform_id,
                platform_open_id="wechat-local:test-account:private:customer",
                service_mode="auto",
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
            )
        )
        db.flush()
        db.add(
            VisitorAssignmentHistory(
                project_id=project_id,
                visitor_id=visitor_id,
                assigned_staff_id=staff_id,
                session_id=session_id,
                source="manual",
            )
        )
        db.add(
            ChannelMember(
                project_id=project_id,
                channel_id=f"{visitor_id}-vtr",
                channel_type=251,
                member_id=staff_id,
                member_type="staff",
            )
        )
        db.commit()
    target = StaffMessageTarget(
        project_id=project_id,
        platform_id=platform_id,
        visitor_id=visitor_id,
        staff_id=staff_id,
        platform_type="wechat_local",
        platform_api_key="test-only-platform",
        platform_open_id="wechat-local:test-account:private:customer",
        channel_id=f"{visitor_id}-vtr",
        channel_type=251,
    )

    def enqueue(number: str):
        with Session(engine) as db:
            request = StaffDeliveryRequest(
                channel_id=target.channel_id,
                client_msg_no=number,
                payload={"type": 1, "content": "Synthetic human reply"},
            )
            row = service.enqueue(db, target, request)
            assert row.history_status == "pending" and row.external_status == "queued"
            return row.id

    with Session(engine) as db:
        now = utc_now()
        turn = CustomerTurn(
            project_id=project_id,
            platform_id=platform_id,
            visitor_id=visitor_id,
            channel_id=target.channel_id,
            actor_key="isolated-customer",
            client_msg_no="ai-not-sent",
            state="ready",
            external_delivery=True,
            created_at=now,
            updated_at=now,
            collect_started_at=now,
            due_at=now,
            reply_text="AI old unsent text",
        )
        db.add(turn)
        db.commit()
        with patch("app.services.customer_turn_ingress.stop_run", AsyncMock()):
            result = asyncio.run(
                staff_delivery.deliver(
                    db,
                    target,
                    StaffDeliveryRequest(
                        channel_id=target.channel_id,
                        client_msg_no="human-1",
                        payload={"type": 1, "content": "Synthetic human reply"},
                    ),
                )
            )
        assert (
            result.delivery_status == "pending"
            and result.delivery_transport == "wechat_local"
        )
        assert db.get(CustomerTurn, turn.id).state == "cancelled"
        delivery_id = db.scalar(
            select(StaffMessageDelivery.id).where(
                StaffMessageDelivery.client_msg_no == "human-1"
            )
        )
    print("PASS human admission cancels unsent AI before bridge publication")
    try:
        migrate("downgrade")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Downgrade must refuse an unresolved external send")

    # Normal messages update the session's timestamp; ownership remains intact.
    with Session(engine) as db:
        owner = db.get(VisitorSession, session_id)
        owner.updated_at = utc_now().replace(tzinfo=None) + timedelta(seconds=5)
        owner.message_count += 1
        db.commit()
        assert (
            service.ready(db, platform_id, db.get(Visitor, visitor_id))[0].delivery_id
            == delivery_id
        )
    barrier = Barrier(2)
    attempts = [uuid4(), uuid4()]

    def race(attempt):
        with Session(engine) as db:
            visitor = db.get(Visitor, visitor_id)
            barrier.wait(timeout=10)
            try:
                return attempt, service.claim(
                    db, delivery_id, platform_id, visitor, attempt
                )
            except HTTPException as error:
                assert error.status_code == 409
                return attempt, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(race, attempts))
    winners = [
        (attempt, claim_id) for attempt, claim_id in outcomes if claim_id is not None
    ]
    assert len(winners) == 1
    attempt, claim_id = winners[0]
    with Session(engine) as db:
        assert (
            service.claim(
                db, delivery_id, platform_id, db.get(Visitor, visitor_id), attempt
            )
            == claim_id
        )
    print("PASS real PostgreSQL concurrent claim and idempotent retry")

    history = AsyncMock()
    with patch.object(staff_delivery, "write_delivery_history", history):
        for _ in range(2):
            with Session(engine) as db:
                asyncio.run(
                    service.confirm(
                        db,
                        delivery_id,
                        platform_id,
                        db.get(Visitor, visitor_id),
                        claim_id,
                        "sent",
                        "test-message-proof-1",
                    )
                )
    assert history.await_count == 1
    with Session(engine) as db:
        row = db.get(StaffMessageDelivery, delivery_id)
        assert row.external_status == "sent" and row.history_status == "sent"
        assert row.payload["extra"]["wechat_local_delivery_id"] == str(delivery_id)
    print("PASS real receipt required and duplicate receipt writes history once")

    old_id = enqueue("human-before-transfer")
    # B -> C -> B must invalidate an old queued B reply even with same final staff.
    for new_owner in [next_staff_id, staff_id]:
        with Session(engine) as db:
            db.scalar(select(Visitor).where(Visitor.id == visitor_id).with_for_update())
            owner = db.scalar(
                select(VisitorSession)
                .where(VisitorSession.id == session_id)
                .with_for_update()
            )
            owner.staff_id = new_owner
            db.add(
                VisitorAssignmentHistory(
                    project_id=project_id,
                    visitor_id=visitor_id,
                    assigned_staff_id=new_owner,
                    session_id=session_id,
                    source="manual",
                )
            )
            db.commit()
    with Session(engine) as db:
        try:
            service.claim(db, old_id, platform_id, db.get(Visitor, visitor_id), uuid4())
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Old assignment reply must not be sent after transfer")
        assert db.get(WeChatStaffDelivery, old_id).state == "cancelled"
    print("PASS B-to-C-to-B invalidates stale intent, ordinary activity does not")

    uncertain_id = enqueue("uncertain-send")
    with Session(engine) as db:
        visitor = db.get(Visitor, visitor_id)
        uncertain_attempt = uuid4()
        uncertain_claim = service.claim(
            db, uncertain_id, platform_id, visitor, uncertain_attempt
        )
        row = db.get(WeChatStaffDelivery, uncertain_id)
        row.claimed_at = utc_now() - timedelta(seconds=121)
        db.commit()
        assert service.ready(db, platform_id, visitor) == []
        assert row.state == "unknown"
        with patch.object(staff_delivery, "write_delivery_history", AsyncMock()):
            asyncio.run(
                service.confirm(
                    db,
                    uncertain_id,
                    platform_id,
                    visitor,
                    uncertain_claim,
                    "sent",
                    "test-late-proof",
                )
            )
        assert db.get(WeChatStaffDelivery, uncertain_id).state == "sent"
    print("PASS claim expiry never reissues, late real receipt settles unknown")
    # Queue origin survives all cloud assignment operations.
    with Session(engine) as db:
        for row in db.scalars(select(WeChatStaffDelivery)):
            assert (
                row.platform_id == platform_id
                and row.platform_open_id == target.platform_open_id
            )
    engine.dispose()
    print("PASS isolated WeChat staff delivery integration; no real messages sent")


if __name__ == "__main__":
    run()

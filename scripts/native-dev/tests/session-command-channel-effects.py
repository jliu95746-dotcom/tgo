"""Exercise committed channel effects against the local WuKongIM service.

All SQL rows live in a rolled-back schema. The random IM channel is deleted.
Sent synthetic messages may remain in IM storage after channel deletion.
No real tenant, customer, or external messaging channel is used.
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "repos/tgo-api"))

from app.core.config import settings  # noqa: E402
from app.core.database import sync_engine  # noqa: E402
from app.models import (  # noqa: E402
    ChannelMember,
    Platform,
    Project,
    SessionCommandDelivery,
    Staff,
    Visitor,
    VisitorSession,
)
from app.services.session_command_membership import (  # noqa: E402
    deliver_channel_effects,
    stage_channel_effects,
)
from app.services.wukongim_client import wukongim_client as im  # noqa: E402
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_STAFF  # noqa: E402
from app.utils.encoding import build_visitor_channel_id  # noqa: E402


async def wait_conversation(uid: str, channel_id: str, present: bool) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        rows = await im.sync_conversations(uid=uid)
        found = any(
            row.channel_id == channel_id
            and row.channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE
            for row in rows
        )
        if found == present:
            return
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Synthetic IM conversation state did not converge")
        await asyncio.sleep(0.1)


def seed(factory, channel_id: str, visitor_id: UUID) -> tuple[UUID, str, str]:
    with factory() as db:
        project = Project(name="session-effects-" + uuid4().hex, api_key=uuid4().hex)
        db.add(project)
        db.flush()
        platform = Platform(
            project_id=project.id, name="synthetic", type="website",
            api_key=uuid4().hex, is_active=True,
        )
        db.add(platform)
        db.flush()
        visitor = Visitor(
            id=visitor_id, project_id=project.id,
            platform_id=platform.id, platform_open_id=uuid4().hex,
        )
        previous = Staff(
            project_id=project.id, username="fixture-" + uuid4().hex,
            password_hash="cannot-login-fixture", role="user",
            account_enabled=True, is_active=True, service_paused=False,
        )
        target = Staff(
            project_id=project.id, username="fixture-" + uuid4().hex,
            password_hash="cannot-login-fixture", role="user",
            account_enabled=True, is_active=True, service_paused=False,
        )
        db.add_all((visitor, previous, target))
        db.flush()
        session = VisitorSession(
            project_id=project.id, visitor_id=visitor.id,
            platform_id=platform.id, staff_id=target.id, status="open",
        )
        db.add(session)
        db.add(ChannelMember(
            project_id=project.id, channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            member_id=previous.id, member_type=MEMBER_TYPE_STAFF,
        ))
        db.flush()
        effects = stage_channel_effects(
            db, project_id=project.id, visitor_id=visitor.id,
            previous_staff_id=previous.id, target_staff_id=target.id,
        )
        operation = SessionCommandDelivery(
            project_id=project.id, visitor_id=visitor.id,
            session_id=session.id, actor_staff_id=previous.id,
            from_staff_id=previous.id, to_staff_id=target.id,
            kind="transfer", session_version=session.updated_at,
            actor_role="user", actor_token_version=1,
            actor_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            client_msg_no="fixture-" + uuid4().hex,
            channel_id=channel_id, channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            from_uid=f"{target.id}-staff", payload={"type": 1002, "content": "fixture"},
            status="skipped", effects=effects, effects_status="pending",
        )
        db.add(operation)
        db.commit()
        return operation.id, f"{previous.id}-staff", f"{target.id}-staff"


async def main() -> None:
    assert settings.WUKONGIM_ENABLED, "IM must be enabled"
    assert urlsplit(im.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
    logging.disable(logging.CRITICAL)
    visitor_id = uuid4()
    channel_id = build_visitor_channel_id(visitor_id)
    schema = "session_effects_" + uuid4().hex
    channel_created = False
    previous_uid = target_uid = None
    with sync_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            for model in (
                Project, Staff, Platform, Visitor, VisitorSession,
                ChannelMember, SessionCommandDelivery,
            ):
                model.__table__.create(connection)
            factory = sessionmaker(
                bind=connection, join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            operation_id, previous_uid, target_uid = seed(
                factory, channel_id, visitor_id,
            )
            await im.create_channel(
                channel_id=channel_id, channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                subscribers=[previous_uid, target_uid, channel_id],
            )
            channel_created = True
            await im.send_message(
                from_uid=channel_id, channel_id=channel_id,
                channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                no_persist=False, payload={"type": 1, "content": "fixture"},
            )
            await wait_conversation(previous_uid, channel_id, True)
            await wait_conversation(target_uid, channel_id, True)

            with factory() as db:
                operation = db.get(SessionCommandDelivery, operation_id)
                assert operation is not None
                session = db.get(VisitorSession, operation.session_id)
                assert session is not None
                session.staff_id = operation.from_staff_id
                db.commit()
            with factory() as db:
                assert await deliver_channel_effects(db, operation_id) == "skipped"
            await wait_conversation(previous_uid, channel_id, True)
            await wait_conversation(target_uid, channel_id, True)
            with factory() as db:
                operation = db.get(SessionCommandDelivery, operation_id)
                assert operation is not None
                session = db.get(VisitorSession, operation.session_id)
                assert session is not None
                session.staff_id = operation.to_staff_id
                operation.effects_status = "pending"
                db.commit()

            with factory() as db:
                assert await deliver_channel_effects(db, operation_id) == "done"
            await wait_conversation(previous_uid, channel_id, False)
            await wait_conversation(target_uid, channel_id, True)
            with factory() as db:
                operation = db.get(SessionCommandDelivery, operation_id)
                assert operation is not None and operation.effects_attempts == 2
                # A crash after the IM calls but before the status commit
                # would replay the original operations under current ownership.
                operation.effects_status = "unknown"
                db.commit()
            with factory() as db:
                assert await deliver_channel_effects(db, operation_id) == "done"
                operation = db.get(SessionCommandDelivery, operation_id)
                assert operation is not None and operation.effects_attempts == 3
            await wait_conversation(previous_uid, channel_id, False)
            await wait_conversation(target_uid, channel_id, True)

            with factory() as db:
                transfer = db.get(SessionCommandDelivery, operation_id)
                assert transfer is not None and transfer.to_staff_id is not None
                session = db.get(VisitorSession, transfer.session_id)
                assert session is not None
                session.status = "closed"
                effects = stage_channel_effects(
                    db, project_id=transfer.project_id,
                    visitor_id=transfer.visitor_id,
                    previous_staff_id=transfer.to_staff_id,
                    target_staff_id=None,
                )
                close = SessionCommandDelivery(
                    project_id=transfer.project_id,
                    visitor_id=transfer.visitor_id,
                    session_id=session.id,
                    actor_staff_id=transfer.to_staff_id,
                    from_staff_id=transfer.to_staff_id,
                    kind="close", session_version=session.updated_at,
                    actor_role="user", actor_token_version=1,
                    actor_token_expires_at=(
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ),
                    client_msg_no="fixture-" + uuid4().hex,
                    channel_id=channel_id,
                    channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                    from_uid=target_uid,
                    payload={"type": 1001, "content": "fixture"},
                    status="skipped", effects=effects, effects_status="pending",
                )
                db.add(close)
                db.commit()
                close_id = close.id
            with factory() as db:
                assert await deliver_channel_effects(db, close_id) == "done"
            await wait_conversation(target_uid, channel_id, False)
            with factory() as db:
                close = db.get(SessionCommandDelivery, close_id)
                assert close is not None
                close.effects_status = "unknown"
                db.commit()
            with factory() as db:
                assert await deliver_channel_effects(db, close_id) == "done"
            await wait_conversation(target_uid, channel_id, False)
            sys.stdout.write(
                "PASS: real IM stale-owner guard, transfer and close effects; "
                "uncertain replays converge without reactivating old staff.\n"
            )
        finally:
            cleanup_errors = []
            if channel_created:
                for uid in (previous_uid, target_uid, channel_id):
                    if uid is None:
                        continue
                    try:
                        await im.delete_conversation(
                            uid=uid, channel_id=channel_id,
                            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
                        )
                    except Exception:
                        cleanup_errors.append("synthetic conversation")
                async with httpx.AsyncClient(
                    base_url=im.base_url, timeout=10, trust_env=False,
                ) as client:
                    try:
                        result = await client.post(
                            "/channel/delete",
                            json={"channel_id": channel_id,
                                  "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE},
                        )
                        result.raise_for_status()
                    except Exception:
                        cleanup_errors.append("synthetic channel")
            transaction.rollback()
            await im.aclose()
            assert not cleanup_errors, cleanup_errors
    with sync_engine.connect() as check:
        assert check.scalar(text(
            "SELECT count(*) FROM pg_namespace WHERE nspname=:schema"
        ), {"schema": schema}) == 0
    sys.stdout.write("PASS: synthetic IM channel removed and SQL rolled back.\n")


if __name__ == "__main__":
    asyncio.run(main())

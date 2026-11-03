"""Recover committed transfer/close effects in separate processes on real IM.

Creates an isolated PostgreSQL database and a random WuKongIM channel. Both
are removed in finally; synthetic message storage is not physically purged.
No tenant or customer data is used.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
API_ROOT = ROOT / "repos/tgo-api"
sys.path.insert(0, str(API_ROOT))

from app.core.config import settings  # noqa: E402
from app.models import (  # noqa: E402
    ChannelMember, Platform, Project, SessionCommandDelivery,
    Staff, Visitor, VisitorSession,
)
from app.services.session_command_membership import (  # noqa: E402
    recover_channel_effects, stage_channel_effects,
)
from app.services.wukongim_client import wukongim_client as im  # noqa: E402
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_STAFF  # noqa: E402
from app.utils.encoding import build_visitor_channel_id  # noqa: E402


CHILD = """
import asyncio
import sys
from uuid import UUID
from app.core.database import SessionLocal
from app.models import SessionCommandDelivery
from app.services.session_command_membership import recover_channel_effects

async def run():
    operation_id = UUID(sys.argv[1])
    with SessionLocal() as db:
        processed = await recover_channel_effects(db, limit=10)
        row = db.get(SessionCommandDelivery, operation_id)
        assert row is not None
        print('RECOVERY', processed, row.kind, row.effects_status, row.effects_attempts)

asyncio.run(run())
"""


CRASH_AFTER_DELETE = """
import asyncio
import os
from app.core.database import SessionLocal
from app.services.session_command_membership import recover_channel_effects
from app.services.wukongim_client import WuKongIMClient, wukongim_client as im

original_delete = WuKongIMClient.delete_conversation

async def delete_then_crash(*args, **kwargs):
    await original_delete(im, *args, **kwargs)
    os._exit(71)

im.delete_conversation = delete_then_crash

async def run():
    with SessionLocal() as db:
        await recover_channel_effects(db, limit=10)

asyncio.run(run())
"""


def make_operation(
    db: Session, *, project_id, visitor_id, session, actor_id,
    previous_id, target_id, kind: str,
) -> SessionCommandDelivery:
    effects = stage_channel_effects(
        db, project_id=project_id, visitor_id=visitor_id,
        previous_staff_id=previous_id, target_staff_id=target_id,
    )
    row = SessionCommandDelivery(
        project_id=project_id, visitor_id=visitor_id,
        session_id=session.id, actor_staff_id=actor_id,
        from_staff_id=previous_id, to_staff_id=target_id,
        kind=kind, session_version=session.updated_at,
        actor_role="user", actor_token_version=1,
        actor_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        client_msg_no="fixture-" + uuid4().hex,
        channel_id=build_visitor_channel_id(visitor_id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        from_uid=f"{actor_id}-staff",
        payload={"type": 1002 if kind == "transfer" else 1001,
                 "content": "fixture"},
        status="skipped", effects=effects, effects_status="pending",
    )
    db.add(row)
    db.flush()
    return row


async def recover_in_child(
    database_url, operation_id, *, crash_after_delete: bool = False,
) -> bytes:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url.render_as_string(hide_password=False)
    env["WUKONGIM_ENABLED"] = "true"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c",
        CRASH_AFTER_DELETE if crash_after_delete else CHILD,
        str(operation_id),
        cwd=str(API_ROOT), env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise
    expected = 71 if crash_after_delete else 0
    if process.returncode != expected:
        raise AssertionError(
            f"Recovery process returned {process.returncode}, expected {expected}: "
            f"{stderr.decode(errors='replace')[-1200:]}"
        )
    if not crash_after_delete:
        assert b"RECOVERY" in stdout, stdout
    return stdout


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
            raise AssertionError("Synthetic conversation state did not converge")
        await asyncio.sleep(0.1)


async def main() -> None:
    from sqlalchemy.engine import make_url
    source_url = make_url(str(settings.DATABASE_URL))
    assert source_url.host in {"127.0.0.1", "localhost", "::1"}
    assert source_url.port == 15432 and source_url.database == "tgo"
    assert settings.WUKONGIM_ENABLED
    assert urlsplit(im.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
    database = "tgo_scenarios_test_" + uuid4().hex[:16]
    assert database.startswith("tgo_scenarios_test_")
    sync_url = source_url.set(
        drivername="postgresql+psycopg2", database=database,
    )
    child_url = source_url.set(database=database)
    admin = create_engine(
        source_url.set(drivername="postgresql+psycopg2", database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    test_engine = None
    created = channel_created = False
    visitor_id = uuid4()
    channel_id = build_visitor_channel_id(visitor_id)
    old_uid = target_uid = None
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
        created = True
        test_engine = create_engine(sync_url)
        with test_engine.begin() as connection:
            for model in (
                Project, Staff, Platform, Visitor, VisitorSession,
                ChannelMember, SessionCommandDelivery,
            ):
                model.__table__.create(connection)
        with Session(test_engine) as db:
            project = Project(name="restart-" + uuid4().hex, api_key=uuid4().hex)
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
            old_uid, target_uid = f"{previous.id}-staff", f"{target.id}-staff"
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
            transfer = make_operation(
                db, project_id=project.id, visitor_id=visitor.id,
                session=session, actor_id=previous.id,
                previous_id=previous.id, target_id=target.id,
                kind="transfer",
            )
            transfer_id = transfer.id
            db.commit()

        await im.create_channel(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            subscribers=[old_uid, target_uid, channel_id],
        )
        channel_created = True
        await im.send_message(
            from_uid=channel_id, channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            no_persist=False, payload={"type": 1, "content": "fixture"},
        )
        await wait_conversation(old_uid, channel_id, True)
        await wait_conversation(target_uid, channel_id, True)
        entered_remote_call = asyncio.Event()
        release_remote_call = asyncio.Event()
        original_remove = im.remove_channel_subscribers

        async def pause_before_remove(*args, **kwargs):
            entered_remote_call.set()
            await release_remote_call.wait()
            return await original_remove(*args, **kwargs)

        async def recover_in_parent() -> int:
            with Session(test_engine) as db:
                return await recover_channel_effects(db, limit=10)

        im.remove_channel_subscribers = pause_before_remove
        parent_task = asyncio.create_task(recover_in_parent())
        try:
            await asyncio.wait_for(entered_remote_call.wait(), timeout=10)
            child_result = await recover_in_child(child_url, transfer_id)
            assert b"RECOVERY" in child_result
        finally:
            release_remote_call.set()
            await parent_task
            im.remove_channel_subscribers = original_remove
        assert parent_task.result() == 1
        await wait_conversation(old_uid, channel_id, False)
        await wait_conversation(target_uid, channel_id, True)

        with Session(test_engine) as db:
            transfer = db.get(SessionCommandDelivery, transfer_id)
            assert transfer is not None
            assert transfer.effects_status == "done" and transfer.effects_attempts == 1

        # Recreate a stale IM conversation and interrupt a replay immediately
        # after the remote delete, before the local transaction can commit.
        await im.add_channel_subscribers(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            subscribers=[old_uid],
        )
        await im.send_message(
            from_uid=channel_id, channel_id=channel_id,
            channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
            no_persist=False, payload={"type": 1, "content": "crash-fixture"},
        )
        await wait_conversation(old_uid, channel_id, True)
        with Session(test_engine) as db:
            transfer = db.get(SessionCommandDelivery, transfer_id)
            assert transfer is not None
            transfer.effects_status = "pending"
            db.commit()
        await recover_in_child(child_url, transfer_id, crash_after_delete=True)
        await wait_conversation(old_uid, channel_id, False)
        with Session(test_engine) as db:
            transfer = db.get(SessionCommandDelivery, transfer_id)
            assert transfer is not None
            assert transfer.effects_status == "pending" and transfer.effects_attempts == 1
        await recover_in_child(child_url, transfer_id)
        await wait_conversation(old_uid, channel_id, False)
        await wait_conversation(target_uid, channel_id, True)

        with Session(test_engine) as db:
            transfer = db.get(SessionCommandDelivery, transfer_id)
            assert transfer is not None and transfer.effects_attempts == 2
            transfer.effects_status = "unknown"
            transfer.effects_updated_at = datetime.now(timezone.utc) - timedelta(seconds=31)
            db.commit()
        await recover_in_child(child_url, transfer_id)
        await wait_conversation(old_uid, channel_id, False)
        await wait_conversation(target_uid, channel_id, True)

        with Session(test_engine) as db:
            transfer = db.get(SessionCommandDelivery, transfer_id)
            assert transfer is not None and transfer.to_staff_id is not None
            session = db.get(VisitorSession, transfer.session_id)
            assert session is not None
            session.status = "closed"
            close = make_operation(
                db, project_id=transfer.project_id,
                visitor_id=transfer.visitor_id, session=session,
                actor_id=transfer.to_staff_id,
                previous_id=transfer.to_staff_id,
                target_id=None, kind="close",
            )
            close_id = close.id
            db.commit()
        await recover_in_child(child_url, close_id)
        await wait_conversation(target_uid, channel_id, False)
        with Session(test_engine) as db:
            close = db.get(SessionCommandDelivery, close_id)
            assert close is not None and close.effects_status == "done"
        print(
            "PASS: two-process contention, crash after remote delete, "
            "unknown replay and close recovered from committed state"
        )
    finally:
        cleanup_errors = []
        if channel_created:
            for uid in (old_uid, target_uid, channel_id):
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
                    response = await client.post(
                        "/channel/delete",
                        json={"channel_id": channel_id,
                              "channel_type": CHANNEL_TYPE_CUSTOMER_SERVICE},
                    )
                    response.raise_for_status()
                except Exception:
                    cleanup_errors.append("synthetic channel")
        await im.aclose()
        if test_engine is not None:
            test_engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
        admin.dispose()
        assert not cleanup_errors, cleanup_errors
    print("PASS: synthetic IM channel and isolated PostgreSQL database removed")


if __name__ == "__main__":
    asyncio.run(main())

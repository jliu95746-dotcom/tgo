"""Opt-in real PostgreSQL reply-context check; no provider/customer messages."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "repos/tgo-platform"))

from app.core.config import settings  # noqa: E402
from app.db.models import DingTalkInbox, FeishuInbox, Platform  # noqa: E402
from app.domain.services.staff_reply_context import (  # noqa: E402
    StaffReplyContextError,
    build_staff_reply_message,
)


async def verify() -> None:
    if make_url(settings.database_url).host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This smoke test only allows a local database")
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    own_platform_ids = [uuid4(), uuid4(), uuid4()]
    recipient = f"staff-context-test-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    try:
        async with sessions() as db:
            try:
                platforms = [
                    Platform(
                        id=pid,
                        project_id=uuid4(),
                        name="staff-context-e2e",
                        type=kind,
                        is_active=False,
                        api_key=uuid4().hex,
                        config={"app_id": "test-only", "app_secret": "test-only"},
                    )
                    for pid, kind in zip(
                        own_platform_ids, ["feishu_bot", "dingtalk_bot", "dingtalk_bot"]
                    )
                ]
                db.add_all(platforms)
                await db.flush()
                db.add(
                    FeishuInbox(
                        platform_id=platforms[0].id,
                        message_id="feishu-peer",
                        from_user=recipient,
                        msg_type="text",
                        content="test",
                        chat_type="p2p",
                        received_at=now,
                    )
                )
                latest = DingTalkInbox(
                    platform_id=platforms[1].id,
                    message_id="dingtalk-peer",
                    from_user=recipient,
                    msg_type="text",
                    content="test",
                    conversation_type="1",
                    received_at=now,
                    session_webhook="https://example.invalid/correct-peer",
                    session_webhook_expired_time=int(
                        (now + timedelta(minutes=5)).timestamp() * 1000
                    ),
                )
                db.add(latest)
                # A newer callback for the same external user on another platform
                # must never become the current conversation's recipient.
                db.add(
                    DingTalkInbox(
                        platform_id=platforms[2].id,
                        message_id="wrong-platform",
                        from_user=recipient,
                        msg_type="text",
                        content="test",
                        conversation_type="1",
                        received_at=now + timedelta(seconds=30),
                        session_webhook="https://example.invalid/wrong-platform",
                        session_webhook_expired_time=int(
                            (now + timedelta(minutes=5)).timestamp() * 1000
                        ),
                    )
                )
                db.add(
                    DingTalkInbox(
                        platform_id=platforms[1].id,
                        message_id="missing-timestamp",
                        from_user=recipient,
                        msg_type="text",
                        content="test",
                        conversation_type="1",
                        received_at=None,
                        session_webhook="https://example.invalid/missing-timestamp",
                    )
                )
                await db.flush()
                for platform, key, field, expected in [
                    (platforms[0], "feishu", "message_id", "feishu-peer"),
                    (
                        platforms[1],
                        "dingtalk",
                        "session_webhook",
                        "https://example.invalid/correct-peer",
                    ),
                ]:
                    message = await build_staff_reply_message(
                        db,
                        platform=platform,
                        recipient=recipient,
                        text="test",
                        client_msg_no="staff-outbound",
                    )
                    assert message.extra[key][field] == expected
                    print(f"PASS real database context selection: {platform.type}")
                latest.session_webhook_expired_time = int(
                    (now - timedelta(seconds=1)).timestamp() * 1000
                )
                await db.flush()
                try:
                    await build_staff_reply_message(
                        db,
                        platform=platforms[1],
                        recipient=recipient,
                        text="test",
                        client_msg_no="staff-outbound",
                    )
                except StaffReplyContextError as exc:
                    assert exc.code == "DELIVERY_CONTEXT_EXPIRED"
                    print(
                        "PASS expired latest context is rejected, no older/wrong-platform fallback"
                    )
                else:
                    raise AssertionError("Expired webhook was accepted")
            finally:
                await db.rollback()
            remaining = await db.scalar(
                select(func.count())
                .select_from(Platform)
                .where(Platform.id.in_(own_platform_ids))
            )
            assert remaining == 0
            print(
                "PASS rollback: no test platform/inbox records persisted; no customer messages sent"
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-local-database", action="store_true", required=True)
    parser.parse_args()
    asyncio.run(verify())

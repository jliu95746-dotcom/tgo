"""Drain persisted incoming channel parts without waiting for an AI answer."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.db.models import Platform
from app.db.turn_context import CustomerTurnContext
from app.domain.entities import ChatCompletionRequest
from app.domain.ports import TgoApiClient
from app.domain.services.turn_context import unseal
from app.domain.services.conversation_scope import upstream_scope

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Registration:
    platform_id: UUID
    source: str
    lease_id: UUID
    fingerprint: str
    request: ChatCompletionRequest


class TurnRegistration:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], client: TgoApiClient
    ):
        self.sessions, self.client = sessions, client
        self.task: asyncio.Task | None = None
        self.jobs: set[asyncio.Task] = set()

    async def claim(self) -> Registration | None:
        now = datetime.now(timezone.utc)
        previous = aliased(CustomerTurnContext)
        async with self.sessions() as db:
            query = (
                select(CustomerTurnContext, Platform)
                .join(Platform, Platform.id == CustomerTurnContext.platform_id)
                .where(
                    Platform.is_active.is_(True),
                    CustomerTurnContext.registered_at.is_(None),
                    CustomerTurnContext.due_at <= now,
                    or_(
                        CustomerTurnContext.lease_until.is_(None),
                        CustomerTurnContext.lease_until < now,
                    ),
                    ~exists(
                        select(previous.source_message_id).where(
                            previous.actor_key == CustomerTurnContext.actor_key,
                            previous.registered_at.is_(None),
                            previous.sequence < CustomerTurnContext.sequence,
                        )
                    ),
                )
                .order_by(CustomerTurnContext.sequence)
                .with_for_update(of=CustomerTurnContext, skip_locked=True)
                .limit(1)
            )
            result = (await db.execute(query)).first()
            if result is None:
                return None
            row, platform = result
            message = unseal(row, platform)
            extra = message.extra or {}
            from app.domain.services.dispatcher import (
                _expected_output_for,
                _default_system_message_for,
                _normalize_message_type,
            )

            request = ChatCompletionRequest(
                api_key=platform.api_key,
                from_uid=message.from_uid,
                message=message.content,
                source_message_id=row.source_message_id,
                msg_type=_normalize_message_type(extra.get("msg_type")),
                media_file_id=extra.get("media_file_id"),
                media_pending=extra.get("media_pending") is True,
                extra=upstream_scope(message),
                media_error=extra.get("media_error"),
                system_message=extra.get("system_message")
                or _default_system_message_for(platform.type),
                expected_output=_expected_output_for(platform.type),
                visitor_name=extra.get("visitor_name"),
                visitor_avatar=extra.get("visitor_avatar"),
            )
            if (
                request.msg_type in (2, 4)
                and not request.media_pending
                and not request.media_file_id
            ):
                from urllib.parse import urlsplit

                if "/v1/chat/files/" not in urlsplit(request.message).path:
                    request.media_error, request.message = "unsupported_media", ""
            row.lease_id, row.lease_until = uuid4(), now + timedelta(seconds=30)
            claimed = Registration(
                platform.id,
                row.source_message_id,
                row.lease_id,
                row.fingerprint,
                request,
            )
            await db.commit()
            return claimed

    async def register(self, item: Registration) -> None:
        accepted = False
        visitor_id = None
        try:
            receipt = await self.client.ingest_turn(item.request)
            if receipt.get("visitor_id"):
                visitor_id = UUID(str(receipt["visitor_id"]))
            accepted = True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning(
                "Channel message registration waiting source=%s error_type=%s",
                item.source[:80],
                type(error).__name__,
            )
        finally:
            async with self.sessions() as db:
                row = await db.get(
                    CustomerTurnContext,
                    (item.platform_id, item.source),
                    with_for_update=True,
                )
                if row and row.lease_id == item.lease_id:
                    row.lease_id, row.lease_until = None, None
                    if row.fingerprint == item.fingerprint:
                        if accepted:
                            row.registered_at = datetime.now(timezone.utc)
                            row.visitor_id = visitor_id
                        else:
                            row.due_at = datetime.now(timezone.utc) + timedelta(
                                seconds=1
                            )
                    await db.commit()

    async def run(self) -> None:
        while True:
            try:
                while len(self.jobs) < 8:
                    item = await self.claim()
                    if item is None:
                        break
                    task = asyncio.create_task(self.register(item))
                    self.jobs.add(task)
                    task.add_done_callback(self.jobs.discard)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Channel registration scheduler waiting: %s", type(error).__name__
                )
            await asyncio.sleep(0.1)

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        tasks = [*self.jobs, *([self.task] if self.task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None

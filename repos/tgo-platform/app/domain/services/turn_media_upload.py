"""Restart-safe Slack uploads; placeholders already fence older answers."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import or_, select

from app.core.config import settings
from app.db.models import Platform
from app.db.turn_context import CustomerTurnContext
from app.domain.entities import NormalizedMessage
from app.domain.services.turn_context import capture, unseal

logger = logging.getLogger(__name__)
MAX_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class MediaUpload:
    platform_id: UUID
    source: str
    lease: UUID
    visitor_id: UUID
    message: NormalizedMessage
    bot_token: str


def safe_slack_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Slack file URL is missing")
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or url.hostname != "files.slack.com"
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        raise ValueError("Untrusted Slack media host")
    return value


class TurnMediaUpload:
    def __init__(self, sessions):
        self.sessions = sessions
        self.task = None
        self.jobs: set[asyncio.Task] = set()

    async def claim(self) -> MediaUpload | None:
        now = datetime.now(timezone.utc)
        async with self.sessions() as db:
            item = (
                await db.execute(
                    select(CustomerTurnContext, Platform)
                    .join(Platform, Platform.id == CustomerTurnContext.platform_id)
                    .where(
                        Platform.is_active.is_(True),
                        Platform.type == "slack",
                        CustomerTurnContext.has_pending_media.is_(True),
                        CustomerTurnContext.visitor_id.is_not(None),
                        or_(
                            CustomerTurnContext.media_lease_until.is_(None),
                            CustomerTurnContext.media_lease_until < now,
                        ),
                    )
                    .order_by(CustomerTurnContext.sequence)
                    .with_for_update(of=CustomerTurnContext, skip_locked=True)
                    .limit(1)
                )
            ).first()
            if item is None:
                return None
            row, platform = item
            row.media_lease_id, row.media_lease_until = uuid4(), now + timedelta(
                seconds=90
            )
            result = MediaUpload(
                platform.id,
                row.source_message_id,
                row.media_lease_id,
                row.visitor_id,
                unseal(row, platform),
                (platform.config or {}).get("bot_token") or "",
            )
            await db.commit()
            return result

    async def transfer(self, item: MediaUpload) -> dict[str, object]:
        incoming = (item.message.extra or {}).get("incoming_media") or {}
        url = safe_slack_url(incoming.get("url"))
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            async with client.stream(
                "GET", url, headers={"Authorization": "Bearer " + item.bot_token}
            ) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ValueError("Media exceeds supported size")
            if not content:
                raise ValueError("Empty media")
            response = await client.post(
                settings.api_base_url.rstrip("/") + "/v1/chat/upload",
                headers={"X-Platform-API-Key": item.message.platform_api_key},
                data={
                    "channel_id": str(item.visitor_id) + "-vtr",
                    "channel_type": "251",
                },
                files={
                    "file": (
                        str(incoming.get("name") or "attachment"),
                        bytes(content),
                        str(incoming.get("mime") or "application/octet-stream"),
                    )
                },
            )
            response.raise_for_status()
            result = response.json()
            if not result.get("file_id") or not result.get("file_url"):
                raise ValueError("Media upload did not return an owned file")
            return result

    async def upload(self, item: MediaUpload) -> None:
        result, failure = None, None
        try:
            result = await self.transfer(item)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            failure = type(error).__name__
            logger.warning(
                "Channel media failed platform=%s error_type=%s",
                item.platform_id,
                failure,
            )
        finally:
            async with self.sessions() as db:
                # Lock the actor before its row, matching capture and send order.
                from sqlalchemy import text

                row = await db.get(CustomerTurnContext, (item.platform_id, item.source))
                if row and db.get_bind().dialect.name == "postgresql":
                    await db.execute(
                        text("SELECT pg_advisory_xact_lock(:key)"),
                        {
                            "key": int.from_bytes(
                                bytes.fromhex(row.actor_key[:16]), "big", signed=True
                            )
                        },
                    )
                    await db.refresh(row)
                if row and row.media_lease_id == item.lease:
                    row.media_lease_id, row.media_lease_until = None, None
                    if result or failure:
                        extra = {**(item.message.extra or {}), "media_pending": False}
                        extra.pop("incoming_media", None)
                        if result:
                            extra["media_file_id"] = result["file_id"]
                        else:
                            extra["media_error"] = "download_failed"
                        updated = item.message.model_copy(
                            update={
                                "content": result["file_url"] if result else "",
                                "extra": extra,
                            }
                        )
                        await capture(db, updated, commit=False)
                    await db.commit()

    def finished(self, task: asyncio.Task) -> None:
        self.jobs.discard(task)
        if not task.cancelled() and task.exception():
            logger.error(
                "Channel media job failed error_type=%s",
                type(task.exception()).__name__,
            )

    async def run(self) -> None:
        while True:
            try:
                while len(self.jobs) < 4:
                    item = await self.claim()
                    if item is None:
                        break
                    task = asyncio.create_task(self.upload(item))
                    self.jobs.add(task)
                    task.add_done_callback(self.finished)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning(
                    "Channel media scheduler waiting: %s", type(error).__name__
                )
            await asyncio.sleep(0.25)

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        tasks = [*self.jobs, *([self.task] if self.task else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None

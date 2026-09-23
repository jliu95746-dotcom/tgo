"""Bounded, authorized message search with accurate pagination metadata."""

import asyncio
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.models import Staff
from app.schemas.search import MessageSearchPagination
from app.schemas.wukongim import WuKongIMSearchResult
from app.services.channel_access import staff_can_access_channel
from app.services.wukongim_client import wukongim_client

SOURCE_PAGE_SIZE = 100


@dataclass
class AuthorizedMessagePage:
    messages: list[WuKongIMSearchResult]
    pagination: MessageSearchPagination


async def search_staff_messages(
    db: Session, user: Staff, keyword: str, page: int, page_size: int,
) -> AuthorizedMessagePage:
    try:
        async with asyncio.timeout(settings.MESSAGE_SEARCH_TIMEOUT_SECONDS):
            return await _scan_messages(db, user, keyword, page, page_size)
    except TimeoutError as exc:
        raise TGOAPIException(
            "搜索耗时较长，请使用更具体的关键词后重试",
            "MESSAGE_SEARCH_TIMEOUT", status_code=504,
        ) from exc


async def _scan_messages(
    db: Session, user: Staff, keyword: str, page: int, page_size: int,
) -> AuthorizedMessagePage:
    start = (page - 1) * page_size
    end = page * page_size
    count = 0
    visible: list[WuKongIMSearchResult] = []
    decisions: dict[tuple[str, int], bool] = {}
    seen: set[int] = set()
    pages = settings.MESSAGE_SEARCH_SCAN_LIMIT // SOURCE_PAGE_SIZE
    for source_page in range(1, pages + 1):
        result = await wukongim_client.search_user_messages(
            uid=f"{user.id}-staff", keyword=keyword,
            page=source_page, limit=SOURCE_PAGE_SIZE,
        )
        for record in result.messages:
            if record.message_id in seen:
                continue
            seen.add(record.message_id)
            key = (record.channel_id, record.channel_type)
            if key not in decisions:
                decisions[key] = await staff_can_access_channel(db, user, *key)
            if not decisions[key]:
                continue
            if start <= count < end:
                visible.append(record)
            count += 1
        exhausted = (
            not result.messages
            or source_page * SOURCE_PAGE_SIZE >= result.total
        )
        if exhausted or count > end:
            return AuthorizedMessagePage(
                messages=visible,
                pagination=MessageSearchPagination(
                    page=page, page_size=page_size,
                    total=count if exhausted else None,
                    has_next=count > end, has_previous=page > 1,
                ),
            )
    # Never return a partial page or label an unverified count as a total.
    raise TGOAPIException(
        "搜索范围较大，请使用更具体的关键词",
        "MESSAGE_SEARCH_LIMIT_EXCEEDED", status_code=422,
    )

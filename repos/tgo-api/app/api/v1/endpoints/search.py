"""Unified search endpoints for visitors and chat messages."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import get_current_active_user
from app.models import Staff, Visitor
from app.schemas import (
    MessageSearchResult,
    SearchPagination,
    SearchScope,
    UnifiedSearchResponse,
    VisitorBasicResponse,
)
from app.core.exceptions import TGOAPIException
from app.schemas.search import MessageSearchPagination
from app.services.message_search import search_staff_messages
from app.services.wukongim_client import wukongim_client

logger = get_logger("endpoints.search")
router = APIRouter()


def _build_message_result(raw: dict) -> dict:
    """Normalize raw WuKongIM search item to API schema."""
    payload_obj = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    if not isinstance(payload_obj, dict):
        payload_obj = {}

    preview_text = None
    if payload_obj:
        preview_text = (
            payload_obj.get("content")
            or payload_obj.get("text")
            or payload_obj.get("title")
            or payload_obj.get("body")
        )

    return {
        "message_id": raw.get("message_id"),
        "client_msg_no": raw.get("client_msg_no"),
        "message_seq": raw.get("message_seq"),
        "from_uid": raw.get("from_uid"),
        "channel_id": raw.get("channel_id"),
        "channel_type": raw.get("channel_type"),
        "timestamp": raw.get("timestamp"),
        "payload": payload_obj,
        "preview_text": preview_text,
    }


@router.get(
    "",
    response_model=UnifiedSearchResponse,
    summary="Unified search across visitors and messages",
    description=(
        "Search visitor profiles stored in TGO and historical chat messages in WuKongIM. "
        "Set scope to filter results to visitors or messages only."
    ),
)
async def unified_search(
    q: str = Query(
        ...,
        min_length=1,
        max_length=255,
        description="Keyword used to match visitor fields or message content",
    ),
    scope: SearchScope = Query(
        SearchScope.ALL,
        description="Search scope: all, visitors, or messages",
    ),
    visitor_page: int = Query(1, ge=1, description="Visitor result page number (1-indexed)"),
    visitor_page_size: int = Query(
        10, ge=1, le=100, description="Number of visitor records per page"
    ),
    message_page: int = Query(1, ge=1, description="Message result page number (1-indexed)"),
    message_page_size: int = Query(
        20, ge=1, le=100, description="Number of message records per page"
    ),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> UnifiedSearchResponse:
    """Unified search endpoint backing the staff console search feature."""
    keyword = q.strip()
    if not keyword:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search keyword cannot be empty",
        )

    logger.info(
        "Unified search invoked",
        extra={
            "staff_id": str(current_user.id),
            "project_id": str(current_user.project_id),
            "scope": scope.value,
            "keyword": keyword,
        },
    )

    visitors: List[VisitorBasicResponse] = []
    visitor_pagination: SearchPagination | None = None
    if scope in (SearchScope.ALL, SearchScope.VISITORS):
        search_term = f"%{keyword}%"
        visitor_query = db.query(Visitor).filter(
            Visitor.project_id == current_user.project_id,
            Visitor.deleted_at.is_(None),
            or_(
                Visitor.name.ilike(search_term),
                Visitor.nickname.ilike(search_term),
                Visitor.platform_open_id.ilike(search_term),
                Visitor.email.ilike(search_term),
                Visitor.phone_number.ilike(search_term),
                Visitor.company.ilike(search_term),
            ),
        )
        visitor_total = visitor_query.count()
        visitor_offset = (visitor_page - 1) * visitor_page_size
        visitor_rows = (
            visitor_query.order_by(Visitor.updated_at.desc())
            .offset(visitor_offset)
            .limit(visitor_page_size)
            .all()
        )
        visitors = [VisitorBasicResponse.model_validate(row) for row in visitor_rows]
        visitor_pagination = SearchPagination(
            page=visitor_page,
            page_size=visitor_page_size,
            total=visitor_total,
        )
    else:
        visitor_pagination = SearchPagination(
            page=visitor_page,
            page_size=visitor_page_size,
            total=0,
        )

    messages: List[MessageSearchResult] = []
    message_pagination: MessageSearchPagination | None = None
    if scope in (SearchScope.ALL, SearchScope.MESSAGES):
        if not wukongim_client.enabled:
            logger.debug("WuKongIM integration disabled; skipping message search")
        else:
            try:
                search_response = await search_staff_messages(
                    db, current_user, keyword, message_page, message_page_size,
                )
            except (HTTPException, TGOAPIException):
                # Propagate downstream errors (already sanitized by client)
                raise
            except Exception as exc:
                logger.error(
                    "WuKongIM message search failed",
                    exc_info=exc,
                    extra={"staff_id": str(current_user.id)},
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="WuKongIM message search failed",
                ) from exc
            else:
                raw_messages = search_response.messages
                for raw in raw_messages:
                    normalized = _build_message_result(raw.model_dump())
                    messages.append(MessageSearchResult.model_validate(normalized))
                message_pagination = search_response.pagination
    if message_pagination is None:
        message_pagination = MessageSearchPagination(
            page=message_page,
            page_size=message_page_size,
            total=0,
            has_next=False,
            has_previous=message_page > 1,
        )

    return UnifiedSearchResponse(
        query=keyword,
        scope=scope,
        visitors=visitors,
        messages=messages,
        visitor_count=len(visitors),
        message_count=len(messages),
        visitor_pagination=visitor_pagination,
        message_pagination=message_pagination,
    )

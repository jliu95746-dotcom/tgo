"""Signed, bounded HTTP ingress for events emitted by internal services."""

import hashlib
import hmac
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.event_trigger import WorkflowEventAccepted, WorkflowEventRequest, WorkflowEventTestContext
from app.services.event_trigger import accept_event, event_test_context


router = APIRouter()
MAX_EVENT_BYTES = 65536
SIGNATURE_WINDOW_SECONDS = 300
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SIGNATURE_PATTERN = re.compile(r"^sha256=[0-9a-f]{64}$")


@router.get("/{workflow_id}/event-test-context", response_model=WorkflowEventTestContext)
async def get_event_test_context(
    workflow_id: str, response: Response,
    project_id: str = Query(..., min_length=1, max_length=128),
    db: AsyncSession = Depends(get_db),
    x_internal_api_key: str | None = Header(default=None),
) -> WorkflowEventTestContext:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise HTTPException(503, "内部事件核验密钥未配置。")
    if not x_internal_api_key or not hmac.compare_digest(x_internal_api_key.encode(), key.get_secret_value().encode()):
        raise HTTPException(403, "内部事件身份无法确认。")
    response.headers["Cache-Control"] = "no-store"
    return await event_test_context(db, workflow_id, project_id)


@router.post("/{workflow_id}/events", response_model=WorkflowEventAccepted,
             status_code=202)
async def trigger_internal_event(
    workflow_id: str,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    x_workflow_timestamp: str | None = Header(default=None),
    x_workflow_event_id: str | None = Header(default=None),
    x_workflow_signature: str | None = Header(default=None),
) -> WorkflowEventAccepted:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise HTTPException(503, "内部事件核验密钥未配置。")
    if (
        x_workflow_timestamp is None or not x_workflow_timestamp.isascii()
        or not x_workflow_timestamp.isdecimal() or len(x_workflow_timestamp) > 12
        or x_workflow_event_id is None
        or EVENT_ID_PATTERN.fullmatch(x_workflow_event_id) is None
        or x_workflow_signature is None
        or SIGNATURE_PATTERN.fullmatch(x_workflow_signature) is None
    ):
        raise HTTPException(403, "内部事件签名无效。")
    if abs(int(time.time()) - int(x_workflow_timestamp)) > SIGNATURE_WINDOW_SECONDS:
        raise HTTPException(403, "内部事件签名已过期。")
    content_length = http_request.headers.get("content-length")
    if content_length and content_length.isdecimal() and int(content_length) > MAX_EVENT_BYTES:
        raise HTTPException(413, "事件数据过大。")
    collected = bytearray()
    async for chunk in http_request.stream():
        collected.extend(chunk)
        if len(collected) > MAX_EVENT_BYTES:
            raise HTTPException(413, "事件数据过大。")
    body = bytes(collected)
    signed = (x_workflow_timestamp.encode() + b"\n"
              + x_workflow_event_id.encode() + b"\n"
              + workflow_id.encode() + b"\n" + body)
    expected = hmac.new(key.get_secret_value().encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(x_workflow_signature[7:], expected):
        raise HTTPException(403, "内部事件签名无效。")
    try:
        payload = WorkflowEventRequest.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "内部事件数据格式无效。") from None
    return await accept_event(db, workflow_id, payload, x_workflow_event_id,
                              key.get_secret_value())

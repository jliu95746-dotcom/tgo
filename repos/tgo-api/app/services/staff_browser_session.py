"""Persistent staff browser sessions with a server-enforced idle deadline."""

from datetime import timedelta
from hashlib import sha256
from secrets import token_urlsafe
from time import time
from typing import Awaitable, cast
from uuid import UUID

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    resolve_staff_token,
    verify_token,
)
from app.models import Project, Staff
from app.schemas.staff import StaffLoginResponse, StaffResponse
from app.services.wukongim_client import wukongim_client

logger = get_logger("staff_browser_session")
COOKIE_NAME = "tgo-staff-session"
IDLE_SECONDS = 7 * 24 * 60 * 60
# Atomic reads/touches cannot revive an expired or deleted session.
READ_SESSION = """
local value = redis.call('GET', KEYS[1])
if not value then return nil end
if ARGV[1] == '1' then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
return {value, redis.call('TTL', KEYS[1])}
"""


class BrowserSession(BaseModel):
    staff_id: UUID
    project_id: UUID
    token_version: int


def require_session_request(request: Request) -> None:
    """Cookie mutations require a browser header and a trusted origin."""
    if request.headers.get("x-tgo-session") != "1":
        raise HTTPException(403, "Invalid browser session request")
    origin = request.headers.get("origin")
    if origin and request.headers.get("sec-fetch-site") != "same-origin":
        allowed = {str(request.base_url).rstrip("/")}
        allowed.update(settings.BACKEND_CORS_ORIGINS)
        allowed.discard("*")
        if origin not in allowed:
            raise HTTPException(403, "Invalid browser session origin")


def session_key(secret: str) -> str:
    return f"staff:browser-session:{sha256(secret.encode()).hexdigest()}"


def redis_client() -> Redis:
    if not settings.REDIS_URL:
        raise HTTPException(503, "Login session service is unavailable")
    return cast(
        Redis,
        Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        ),
    )


def set_session_cookie(
    request: Request,
    response: Response,
    secret: str,
) -> None:
    response.set_cookie(
        COOKIE_NAME,
        secret,
        max_age=IDLE_SECONDS,
        path="/",
        httponly=True,
        samesite="strict",
        secure=(
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https"
        ),
    )
    response.headers["Cache-Control"] = "no-store"


async def start_browser_session(
    request: Request,
    response: Response,
    staff: Staff,
) -> str:
    require_session_request(request)
    secret = token_urlsafe(32)
    session = BrowserSession(
        staff_id=staff.id,
        project_id=staff.project_id,
        token_version=staff.token_version,
    )
    try:
        async with redis_client() as client:
            await client.setex(
                session_key(secret), IDLE_SECONDS, session.model_dump_json()
            )
            previous = request.cookies.get(COOKIE_NAME)
            if previous:
                await client.delete(session_key(previous))
    except RedisError as exc:
        raise HTTPException(
            503, "Login session service is unavailable"
        ) from exc
    set_session_cookie(request, response, secret)
    return secret


async def refresh_browser_session(
    request: Request,
    response: Response,
    db: Session,
    active: bool,
) -> StaffLoginResponse:
    require_session_request(request)
    secret = request.cookies.get(COOKIE_NAME)
    if not secret:
        # Upgrade a still-valid login when this feature is first deployed.
        authorization = request.headers.get("authorization", "")
        token = (
            authorization[7:] if authorization.startswith("Bearer ") else ""
        )
        current = resolve_staff_token(db, token) if token else None
        if current is None:
            raise HTTPException(401, "Login session has expired")
        secret = await start_browser_session(request, response, current)
    try:
        async with redis_client() as client:
            result = await cast(
                Awaitable[list[str | int] | None],
                client.eval(
                    READ_SESSION,
                    1,
                    session_key(secret),
                    int(active),
                    IDLE_SECONDS,
                ),
            )
            if not result or int(result[1]) <= 0:
                raise HTTPException(401, "Login session has expired")
            try:
                session = BrowserSession.model_validate_json(str(result[0]))
            except ValidationError as exc:
                raise HTTPException(401, "Invalid login session") from exc
            staff = db.scalar(
                select(Staff).where(
                    Staff.id == session.staff_id,
                    Staff.project_id == session.project_id,
                    Staff.token_version == session.token_version,
                    Staff.account_enabled.is_(True),
                    Staff.deleted_at.is_(None),
                    Staff.project_id.in_(
                        select(Project.id).where(Project.deleted_at.is_(None))
                    ),
                )
            )
            if staff is None:
                await client.delete(session_key(secret))
                raise HTTPException(401, "Login session has been revoked")
            seconds = min(
                settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, int(result[1])
            )
    except RedisError as exc:
        raise HTTPException(
            503, "Login session service is unavailable"
        ) from exc

    authorization = request.headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    claims = verify_token(token) if token else None
    expiration = claims.get("exp") if claims else None
    reusable = (
        claims is not None
        and claims.get("sub") == staff.username
        and claims.get("project_id") == str(staff.project_id)
        and claims.get("token_version") == staff.token_version
        and isinstance(expiration, (int, float))
        and 60 < expiration - time() <= seconds
    )
    if reusable and isinstance(expiration, (int, float)):
        seconds = max(1, int(expiration - time()))
    if not reusable:
        token = create_access_token(
            subject=staff.username,
            project_id=staff.project_id,
            role=staff.role,
            token_version=staff.token_version,
            expires_delta=timedelta(seconds=seconds),
        )
        # WuKongIM uses this access token when the browser reconnects.
        try:
            await wukongim_client.register_or_login_user(
                uid=f"{staff.id}-staff",
                token=token,
            )
        except Exception:
            logger.warning("Unable to synchronize refreshed staff IM token")
    if active:
        set_session_cookie(request, response, secret)
    response.headers["Cache-Control"] = "no-store"
    return StaffLoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=seconds,
        staff=StaffResponse.model_validate(staff),
    )


async def end_browser_session(request: Request, response: Response) -> None:
    require_session_request(request)
    secret = request.cookies.get(COOKIE_NAME)
    if secret:
        try:
            async with redis_client() as client:
                await client.delete(session_key(secret))
        except RedisError as exc:
            raise HTTPException(
                503, "Login session service is unavailable"
            ) from exc
    response.delete_cookie(COOKIE_NAME, path="/", samesite="strict")
    response.headers["Cache-Control"] = "no-store"

from __future__ import annotations

import logging
import json
import hashlib
from enum import Enum
import uuid

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence, Tuple, cast

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.db.models import (
    MediaProcessingJob,
    MessageMedia,
    WeComInbox,
    WeComSyncCursor,
)
from app.db.error_utils import is_expected_unique_violation
from app.domain.services.media.types import MediaType, WeComMediaReference


class InboxStoreResult(str, Enum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    ERROR = "error"


class WeComSyncContinuation(RuntimeError):
    """The page budget was exhausted while more messages remain."""


try:
    from redis import asyncio as aioredis  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    aioredis = None  # type: ignore


# --- Redis client (lazy singleton) -------------------------------------------------
_redis_client = None


async def get_redis_client():
    """Return a cached Redis asyncio client if configured and healthy; else None."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    if not aioredis or not settings.redis_url:
        return None
    client = None
    try:
        client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await client.ping()
        _redis_client = client
        return client
    except Exception as e:  # pragma: no cover
        _redis_client = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
        logging.warning("[WECOM] Redis unavailable: %s", e)
        return None


# --- WeCom token and API wrappers --------------------------------------------------
async def wecom_get_access_token(corp_id: str, app_secret: str, timeout: Optional[int] = None) -> str:
    """Fetch WeCom access_token.

    Raises RuntimeError if WeCom returns an error.
    """
    cache_key = _wecom_token_cache_key(corp_id, app_secret)
    redis = await get_redis_client()
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return str(cached)
        except Exception as exc:
            logging.warning("[WECOM] Access-token cache read failed: %s", exc)

    url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": corp_id, "corpsecret": app_secret}
    try:
        async with httpx.AsyncClient(
            timeout=timeout or settings.request_timeout_seconds
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"WeCom gettoken failed with HTTP {exc.response.status_code}"
        ) from None
    except httpx.HTTPError:
        raise RuntimeError("WeCom gettoken failed due to a network error") from None
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError("WeCom gettoken returned an invalid response") from None

    if not isinstance(data, dict):
        raise RuntimeError("WeCom gettoken returned an invalid response")
    try:
        errcode = int(data.get("errcode") or 0)
    except (TypeError, ValueError):
        raise RuntimeError("WeCom gettoken returned an invalid errcode") from None
    if errcode != 0:
        raise RuntimeError(f"WeCom gettoken returned errcode {errcode}")
    access_token = str(data.get("access_token") or "")
    if not access_token:
        raise RuntimeError("WeCom gettoken response is missing access_token")
    if redis is not None:
        try:
            expires_in = max(60, int(data.get("expires_in") or 7200) - 60)
        except (TypeError, ValueError):
            expires_in = 7140
        try:
            await redis.set(cache_key, access_token, ex=expires_in)
        except Exception as exc:
            logging.warning("[WECOM] Access-token cache write failed: %s", exc)
    return access_token


def _wecom_token_cache_key(corp_id: str, app_secret: str) -> str:
    credential_digest = hashlib.sha256(
        f"{corp_id}\0{app_secret}".encode("utf-8")
    ).hexdigest()[:24]
    return f"wecom:access-token:{credential_digest}"


async def invalidate_wecom_access_token(corp_id: str, app_secret: str) -> None:
    """Invalidate a cached WeCom token after an authentication response."""
    redis = await get_redis_client()
    if redis is None:
        return
    cache_key = _wecom_token_cache_key(corp_id, app_secret)
    try:
        await redis.delete(cache_key)
    except Exception as exc:
        logging.warning("[WECOM] Access-token cache invalidation failed: %s", exc)


async def wecom_upload_temp_media(access_token: str, file_bytes: bytes, media_type: str = "image", filename: Optional[str] = None, content_type: Optional[str] = None) -> str:
    """Upload temporary media to WeCom and return media_id.

    Docs: https://developer.work.weixin.qq.com/document/25551
    Endpoint: POST /cgi-bin/media/upload?access_token=ACCESS_TOKEN&type=image
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type={media_type}"
    fname = filename or ("upload.jpg" if media_type == "image" else "upload.bin")
    ctype = content_type or ("image/jpeg" if media_type == "image" else "application/octet-stream")
    files = {"media": (fname, file_bytes, ctype)}
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        resp = await client.post(url, files=files)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") not in (0, None):
            raise RuntimeError(f"WeCom upload media failed: {data}")
        media_id = data.get("media_id") or data.get("thumb_media_id")
        if not media_id:
            raise RuntimeError(f"WeCom upload media missing media_id: {data}")
        return media_id


async def wecom_kf_sync_msg(access_token: str, open_kf_id: str, cursor: str, event_token: str, limit: int = 500) -> dict:
    """Call KF sync_msg API and return JSON result.

    Required payload params per docs: open_kfid, token; optional: cursor, limit
    See: https://developer.work.weixin.qq.com/document/path/94670
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={access_token}"
    payload: dict[str, object] = {
        "open_kfid": open_kf_id,
        "cursor": cursor or "",
        "limit": int(limit),
        "voice_format": 0,
    }
    if event_token:
        payload["token"] = event_token
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()


# --- Visitor profile APIs (KF + ExternalContact) ---------------------------------
async def _wecom_kf_batch_get_customer_basic(access_token: str, external_userids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Call KF batchget to retrieve basic customer info (nickname, avatar).

    Returns a map: { external_userid: {"nickname": str|None, "avatar": str|None} }
    """
    if not external_userids:
        return {}
    url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/customer/batchget?access_token={access_token}"
    payload = {"external_userid_list": list(external_userids)}
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"kf customer batchget failed: {data}")
        result: Dict[str, Dict[str, Any]] = {}
        for item in (data.get("customer_list", []) or []):
            eu = item.get("external_userid")
            if eu:
                result[eu] = {
                    "nickname": item.get("nickname"),
                    "avatar": item.get("avatar"),
                }
        return result


async def _wecom_externalcontact_get(access_token: str, external_userid: str) -> Dict[str, Any] | None:
    """Fallback: Get external contact detail via customer contact API.

    Returns {"name": str|None, "avatar": str|None} or None on failure.
    """
    url = "https://qyapi.weixin.qq.com/cgi-bin/externalcontact/get"
    params = {"access_token": access_token, "external_userid": external_userid}
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        print("_externalcontact_get-data-->", data)
        if data.get("errcode") != 0:
            return None
        ec = data.get("external_contact") or {}
        return {"name": ec.get("name"), "avatar": ec.get("avatar")}


async def get_wecom_visitor_profile(corp_id: str, app_secret: str, external_userid: str) -> Dict[str, str | None]:
    """Fetch WeCom visitor profile (nickname, avatar) for a given external_userid.

    Strategy:
    - Primary: KF batch customer info API
    - Fallback: external contact detail API
    Returns: {"nickname": str|None, "avatar": str|None}
    """
    try:
        access_token = await wecom_get_access_token(corp_id, app_secret)
    except Exception as e:
        # Propagate minimal info: unable to get token -> return empty profile; caller should degrade.
        print(f"[WECOM] get access token failed: {e}")
        return {"nickname": None, "avatar": None}

    # 1) Try KF batchget (works when external_userid belongs to KF contact)
    try:
        basic_map = await _wecom_kf_batch_get_customer_basic(access_token, [external_userid])
        print("basic_map--->", basic_map)
        info = basic_map.get(external_userid)
        if info:
            return {"nickname": info.get("nickname"), "avatar": info.get("avatar")}
    except Exception as e:
        print(f"[WECOM] kf batchget failed for {external_userid}: {e}")

    # 2) Fallback to customer contact detail
    try:
        ec = await _wecom_externalcontact_get(access_token, external_userid)
        if ec:
            return {"nickname": ec.get("name"), "avatar": ec.get("avatar")}
    except Exception as e:
        print(f"[WECOM] externalcontact get failed for {external_userid}: {e}")

    return {"nickname": None, "avatar": None}

# --- KF send message API -----------------------------------------------------------
async def wecom_kf_send_msg(
    access_token: str,
    open_kfid: str,
    external_userid: str,
    msgtype: str,
    content: dict,
    message_id: str | None = None,
) -> dict:
    """Send a KF message to an external user.

    Docs: https://developer.work.weixin.qq.com/document/path/94677
    Required: open_kfid, touser(external_userid), msgtype, specific content block
    Returns JSON response dict; raises on HTTP errors or WeCom errcode != 0.
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}"
    payload = {
        "open_kfid": open_kfid,
        "touser": external_userid,
        "msgtype": msgtype,
        msgtype: content,
    }
    if message_id:
        payload["msgid"] = message_id
    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"WeCom KF send_msg failed: {data}")
        return data


async def wecom_kf_send_image_msg(access_token: str, open_kfid: str, external_userid: str, media_id: str) -> dict:
    """Convenience wrapper to send KF image message.

    Equivalent to calling wecom_kf_send_msg(..., msgtype="image", content={"media_id": media_id}).
    """
    return await wecom_kf_send_msg(
        access_token,
        open_kfid=open_kfid,
        external_userid=external_userid,
        msgtype="image",
        content={"media_id": media_id},
    )

# --- WeCom Bot (智能机器人) response API via response_url ----------------------
async def wecom_bot_send_response(
    response_url: str,
    msgtype: str,
    content: dict,
    timeout: Optional[int] = None,
) -> dict:
    """Send a response via WeCom Bot response_url (智能机器人主动回复).

    This is used to reply to messages received by the bot. The response_url
    is provided in the incoming message callback.

    Docs: https://developer.work.weixin.qq.com/document/path/101138

    IMPORTANT: The 主动回复消息 API only supports:
    - markdown: {"content": "消息内容"}
    - template_card: {...}

    NOTE: "text" message type is NOT supported by this API!
    Use "markdown" for plain text responses.

    Returns JSON response dict; raises on HTTP errors or WeCom errcode != 0.
    """
    if not response_url:
        raise RuntimeError("WeCom Bot response_url is required")

    payload: Dict[str, Any] = {
        "msgtype": msgtype,
        msgtype: content,
    }

    logging.info("[WECOM_BOT] Sending response to %s, payload=%s", response_url[:80] + "...", json.dumps(payload, ensure_ascii=False)[:200])

    async with httpx.AsyncClient(timeout=timeout or settings.request_timeout_seconds) as client:
        resp = await client.post(response_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        logging.info("[WECOM_BOT] Response result: %s", data)
        if data.get("errcode") not in (0, None):
            raise RuntimeError(f"WeCom Bot response failed: {data}")
        return data


async def wecom_bot_send_response_text(
    response_url: str,
    content: str,
    timeout: Optional[int] = None,
) -> dict:
    """Send text response via WeCom Bot response_url (using markdown format).

    Docs: https://developer.work.weixin.qq.com/document/path/101138

    NOTE: The 主动回复消息 API does NOT support "text" type!
    We use "markdown" type to send plain text responses.

    Args:
        response_url: The response URL from the incoming message
        content: Message text content (max 20480 bytes)
    """
    # Use markdown type since text type is not supported by 主动回复消息 API
    return await wecom_bot_send_response(response_url, msgtype="markdown", content={"content": content[:20480]}, timeout=timeout)


async def wecom_bot_send_response_markdown(
    response_url: str,
    content: str,
    timeout: Optional[int] = None,
) -> dict:
    """Send markdown response via WeCom Bot response_url.

    Docs: https://developer.work.weixin.qq.com/document/path/101138

    Args:
        response_url: The response URL from the incoming message
        content: Markdown content (max 20480 bytes)
    """
    return await wecom_bot_send_response(response_url, msgtype="markdown", content={"content": content[:20480]}, timeout=timeout)


# --- App (colleague) send message API -----------------------------------------
async def wecom_send_app_message(
    access_token: str,
    to_user: str,
    agent_id: int | str,
    msgtype: str,
    content: dict,
    duplicate_check_interval: int | None = 10,
    timeout: Optional[int] = None,
) -> dict:
    """Send an application message to an internal colleague (enterprise member).

    Docs: https://developer.work.weixin.qq.com/document/path/90236
    Endpoint: POST /cgi-bin/message/send
    Raises RuntimeError when WeCom errcode != 0.
    """
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
    payload: Dict[str, Any] = {
        "touser": to_user,
        "agentid": int(agent_id),
        "msgtype": msgtype,
        msgtype: content,
    }
    if duplicate_check_interval is not None:
        payload["duplicate_check_interval"] = int(duplicate_check_interval)

    async with httpx.AsyncClient(timeout=timeout or settings.request_timeout_seconds) as client:
        resp = await client.post(url, content=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"WeCom app send message failed: {data}")
        return data




# --- Visitor/platform resolution helpers -----------------------------------------
async def resolve_visitor_platform_open_id(visitor_id: str) -> str:
    """Resolve a tgo-platform visitor_id to the platform-specific open ID with Redis caching.

    Cache key: visitor:{visitor_id}:platform_open_id
    Fetches from tgo-api GET /v1/visitors/{visitor_id}/basic on cache miss.
    """
    vid = (visitor_id or "").strip()
    if not vid:
        raise RuntimeError("Missing visitor_id to resolve platform_open_id")
    key = f"visitor:{vid}:platform_open_id"
    redis = await get_redis_client()
    if redis:
        try:
            cached = await redis.get(key)
            if cached:
                return cached
        except Exception as e:
            logging.warning("[RESOLVE] Redis get failed for %s: %s", key, e)
    async with httpx.AsyncClient(base_url=settings.api_base_url, timeout=settings.request_timeout_seconds) as client:
        resp = await client.get(f"/v1/visitors/{vid}/basic")
        resp.raise_for_status()
        data = resp.json()
    platform_open_id = (data or {}).get("platform_open_id") or ""
    if not platform_open_id:
        raise RuntimeError("Visitor basic info missing platform_open_id")
    if redis:
        try:
            await redis.set(key, platform_open_id, ex=3600)
        except Exception as e:
            logging.warning("[RESOLVE] Redis set failed for %s: %s", key, e)
    return platform_open_id


async def resolve_wecom_open_kfid(visitor_id: str, platform_id, db: AsyncSession) -> str:
    """Resolve WeCom open_kfid for a visitor on a platform with Redis caching.

    Cache key: wecom:visitor:{visitor_id}:open_kfid
    Looks up latest WeComInbox for (platform_id, from_user == platform_open_id).
    """
    vid = (visitor_id or "").strip()
    if not vid or platform_id is None:
        raise RuntimeError("Missing visitor_id or platform_id to resolve open_kfid")

    cache_key = f"wecom:visitor:{vid}:open_kfid"
    redis = await get_redis_client()
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return cached
        except Exception as e:
            logging.warning("[RESOLVE] Redis get failed for %s: %s", cache_key, e)

    platform_open_id = await resolve_visitor_platform_open_id(vid)

    stmt = (
        select(WeComInbox.open_kfid)
        .where(WeComInbox.platform_id == platform_id, WeComInbox.from_user == platform_open_id)
        .order_by(WeComInbox.received_at.desc(), WeComInbox.fetched_at.desc())
        .limit(1)
    )
    row = await db.execute(stmt)
    r = row.first()
    if not r or not (r[0] or "").strip():
        raise RuntimeError("No WeCom KF conversation found for visitor; cannot resolve open_kfid")

    open_kfid = str(r[0]).strip()
    if redis:
        try:
            await redis.set(cache_key, open_kfid, ex=3600)
        except Exception as e:
            logging.warning("[RESOLVE] Redis set failed for %s: %s", cache_key, e)
    return open_kfid


# --- Shared helpers ---------------------------------------------------------------
def build_xml_raw_payload(raw_xml: str, decrypted_xml: Optional[str], parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Construct a standardized raw_payload for XML-based webhooks."""
    payload: Dict[str, Any] = {"raw_xml": raw_xml}
    if decrypted_xml is not None:
        payload["decrypted_xml"] = decrypted_xml
    payload["parsed"] = parsed
    return payload


async def try_store_wecom_inbox(
    db: AsyncSession,
    *,
    media_reference: WeComMediaReference | None = None,
    **kwargs: Any,
) -> InboxStoreResult:
    """Persist inbox, media metadata, and download job in one transaction."""
    try:
        inbox_id = uuid.uuid4()
        record = WeComInbox(id=inbox_id, **kwargs)
        db.add(record)
        if media_reference is not None:
            media_id = uuid.uuid4()
            media = MessageMedia(
                id=media_id,
                platform_id=kwargs["platform_id"],
                inbox_id=inbox_id,
                source_media_id=media_reference.source_media_id,
                media_type=media_reference.media_type,
                status="pending" if media_reference.supported else "unsupported",
                original_filename=media_reference.original_filename,
                declared_size=media_reference.declared_size,
            )
            db.add(media)
            if media_reference.supported:
                db.add(
                    MediaProcessingJob(
                        id=uuid.uuid4(),
                        media_id=media_id,
                        job_type="download",
                        status="pending",
                        max_attempts=settings.media_job_max_attempts,
                    )
                )
        await db.commit()
        return InboxStoreResult.STORED
    except IntegrityError as exc:
        await db.rollback()
        if is_expected_unique_violation(
            exc,
            "uq_wecom_inbox_platform_message",
        ):
            if media_reference is not None:
                repaired = await _ensure_duplicate_media_state(
                    db,
                    platform_id=kwargs["platform_id"],
                    message_id=kwargs["message_id"],
                    media_reference=media_reference,
                )
                if not repaired:
                    return InboxStoreResult.ERROR
            return InboxStoreResult.DUPLICATE
        logging.error("[WECOM] Inbox integrity error: %s", exc)
        return InboxStoreResult.ERROR
    except Exception as exc:  # pragma: no cover
        await db.rollback()
        logging.error("[WECOM] Failed to store inbox record: %s", exc)
        return InboxStoreResult.ERROR


async def _ensure_duplicate_media_state(
    db: AsyncSession,
    *,
    platform_id: object,
    message_id: str,
    media_reference: WeComMediaReference,
) -> bool:
    """Repair a legacy/partial duplicate that lacks media metadata or a job."""
    try:
        inbox_id = await db.scalar(
            select(WeComInbox.id).where(
                WeComInbox.platform_id == platform_id,
                WeComInbox.message_id == message_id,
            )
        )
        if inbox_id is None:
            return False

        existing_media = await db.scalar(
            select(MessageMedia).where(MessageMedia.inbox_id == inbox_id)
        )
        if existing_media is not None and (
            existing_media.source_media_id != media_reference.source_media_id
            or existing_media.media_type != media_reference.media_type
        ):
            logging.warning(
                "[WECOM] Duplicate message %s changed immutable media fields; "
                "keeping the canonical inbox media",
                message_id,
            )
            return True
        if existing_media is None:
            candidate_media_id = uuid.uuid4()
            media_insert = (
                pg_insert(MessageMedia)
                .values(
                    id=candidate_media_id,
                    platform_id=platform_id,
                    inbox_id=inbox_id,
                    source_media_id=media_reference.source_media_id,
                    media_type=media_reference.media_type,
                    status="pending" if media_reference.supported else "unsupported",
                    original_filename=media_reference.original_filename,
                    declared_size=media_reference.declared_size,
                )
                .on_conflict_do_nothing(index_elements=["inbox_id"])
            )
            await db.execute(media_insert)
        media_id = await db.scalar(
            select(MessageMedia.id).where(MessageMedia.inbox_id == inbox_id)
        )
        if media_id is None:
            await db.rollback()
            return False
        if media_reference.supported:
            job_insert = (
                pg_insert(MediaProcessingJob)
                .values(
                    id=uuid.uuid4(),
                    media_id=media_id,
                    job_type="download",
                    status="pending",
                    max_attempts=settings.media_job_max_attempts,
                )
                .on_conflict_do_nothing(
                    index_elements=["media_id", "job_type"],
                )
            )
            await db.execute(job_insert)
        await db.commit()
        return True
    except Exception as exc:
        await db.rollback()
        logging.error(
            "[WECOM] Failed to repair media state for duplicate message: %s",
            exc,
        )
        return False
def _extract_kf_content(msg: Dict[str, Any]) -> Tuple[str, str, str, Optional[datetime]]:
    """Extract minimal fields from a KF message for inbox storage.

    Returns a tuple: (external_userid, msgtype, content_text, received_at)
    Content text is a readable placeholder for non-text types.
    """
    msgtype = str(msg.get("msgtype") or "unknown")
    external_userid = str(msg.get("external_userid") or "")
    send_time = int(msg.get("send_time") or 0)
    received_at = None
    try:
        if send_time > 0:
            received_at = datetime.fromtimestamp(send_time, tz=timezone.utc)
    except Exception:  # pragma: no cover
        received_at = None

    # Rich content extraction for common types
    content = ""
    if msgtype == "text":
        content = ((msg.get("text") or {}).get("content") or "")
    elif msgtype == "image":
        media_id = ((msg.get("image") or {}).get("media_id") or "")
        content = f"[image]{' ' + media_id if media_id else ''}"
    elif msgtype == "file":
        file_obj = (msg.get("file") or {})
        name = file_obj.get("file_name") or ""
        size = file_obj.get("file_size")
        size_str = f" ({size}B)" if isinstance(size, int) else ""
        content = f"[file]{' ' + name if name else ''}{size_str}"
    elif msgtype == "video":
        media_id = ((msg.get("video") or {}).get("media_id") or "")
        content = f"[video]{' ' + media_id if media_id else ''}"
    elif msgtype == "voice":
        media_id = ((msg.get("voice") or {}).get("media_id") or "")
        content = f"[voice]{' ' + media_id if media_id else ''}"
    elif msgtype == "link":
        link = (msg.get("link") or {})
        title = link.get("title") or ""
        url = link.get("url") or ""
        content = f"[link]{' ' + title if title else ''}{' ' + url if url else ''}"
    else:
        # Fallback placeholder for other types (location, event, etc.)
        content = f"[{msgtype}]"

    return external_userid, msgtype, content, received_at


def _extract_kf_media_reference(msg: Dict[str, Any]) -> WeComMediaReference | None:
    """Extract one typed media reference from a WeCom KF message."""
    msgtype = str(msg.get("msgtype") or "").lower()
    if msgtype not in {"image", "voice", "video", "file"}:
        return None
    media_payload = msg.get(msgtype) or {}
    if not isinstance(media_payload, dict):
        return None
    source_media_id = str(media_payload.get("media_id") or "").strip()
    if not source_media_id or len(source_media_id) > 255:
        return None
    declared_size_raw = media_payload.get("file_size")
    declared_size = None
    if isinstance(declared_size_raw, (int, str)):
        declared_size_text = str(declared_size_raw)
        if declared_size_text.isdigit():
            parsed_size = int(declared_size_text)
            if parsed_size <= 9_223_372_036_854_775_807:
                declared_size = parsed_size
    return WeComMediaReference(
        source_media_id=source_media_id,
        media_type=cast(MediaType, msgtype),
        supported=msgtype in {"image", "voice"},
        original_filename=(
            str(media_payload.get("file_name") or "").strip()[:255] or None
        ),
        declared_size=declared_size,
    )


async def get_wecom_sync_cursor(
    db: AsyncSession,
    platform_id: object,
    open_kf_id: str,
    corp_id: str,
) -> str:
    """Load the durable cursor, importing a legacy Redis cursor once if present."""
    cursor_row = await db.scalar(
        select(WeComSyncCursor).where(
            WeComSyncCursor.platform_id == platform_id,
            WeComSyncCursor.open_kfid == open_kf_id,
        )
    )
    if cursor_row is not None:
        return cursor_row.cursor or ""

    redis = await get_redis_client()
    legacy_cursor = ""
    if redis:
        cursor_key = f"wecom:kf:cursor:{corp_id}:{open_kf_id}".lower()
        try:
            legacy_cursor = await redis.get(cursor_key) or ""
        except Exception as exc:
            logging.warning("[WECOM] Failed to read legacy Redis cursor: %s", exc)

    if legacy_cursor:
        statement = (
            pg_insert(WeComSyncCursor)
            .values(
                id=uuid.uuid4(),
                platform_id=platform_id,
                open_kfid=open_kf_id,
                cursor=legacy_cursor,
            )
            .on_conflict_do_nothing(
                index_elements=["platform_id", "open_kfid"],
            )
        )
        await db.execute(statement)
        await db.commit()
    return legacy_cursor


async def persist_wecom_sync_cursor(
    db: AsyncSession,
    platform_id: object,
    open_kf_id: str,
    corp_id: str,
    cursor: str,
    cursor_ttl_seconds: int,
) -> None:
    """Persist the sync cursor in PostgreSQL; Redis remains a best-effort cache."""
    now = datetime.now(timezone.utc)
    statement = (
        pg_insert(WeComSyncCursor)
        .values(
            id=uuid.uuid4(),
            platform_id=platform_id,
            open_kfid=open_kf_id,
            cursor=cursor,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["platform_id", "open_kfid"],
            set_={"cursor": cursor, "updated_at": now},
        )
    )
    await db.execute(statement)
    await db.commit()

    redis = await get_redis_client()
    if redis:
        cursor_key = f"wecom:kf:cursor:{corp_id}:{open_kf_id}".lower()
        try:
            await redis.set(cursor_key, cursor, ex=cursor_ttl_seconds)
        except Exception as exc:
            logging.warning("[WECOM] Failed to cache cursor in Redis: %s", exc)


# --- KF sync driver ---------------------------------------------------------------
async def sync_kf_messages(
    corp_id: str,
    app_secret: str,
    event_token: str,
    open_kf_id: str,
    platform_id,
    db: AsyncSession,
    *,
    max_iters: int = 10,
    cursor_ttl_seconds: int = 7 * 24 * 60 * 60,
    batch_limit: int = 500,
) -> None:
    """Sync actual KF messages upon receiving kf_msg_or_event.

    - Persists the cursor in PostgreSQL and mirrors it to Redis as a cache
    - Paginates until has_more == 0 or max_iters reached
    - Stores each message into wecom_inbox with enriched content placeholder
    - Logs metrics per page
    """
    access_token = await wecom_get_access_token(corp_id, app_secret)

    cursor = await get_wecom_sync_cursor(db, platform_id, open_kf_id, corp_id)

    has_more = 0
    for page in range(1, max_iters + 1):
        # Call sync
        data = await wecom_kf_sync_msg(
            access_token,
            open_kf_id,
            cursor,
            event_token=event_token,
            limit=batch_limit,
        )

        if data.get("errcode") not in (0, None):
            raise RuntimeError(f"WeCom KF sync API returned error: {data}")

        msg_list = data.get("msg_list") or []
        stored_count = 0
        for msg in msg_list:
            if not isinstance(msg, dict):
                continue
            try:
                origin = int(msg.get("origin") or 0)
            except (TypeError, ValueError):
                origin = 0
            if origin != 3:
                # Only customer-originated messages enter the AI pipeline.
                continue
            msgid = str(msg.get("msgid") or "")
            if not msgid:
                raise RuntimeError("WeCom KF customer message is missing msgid")
            ext_uid, msgtype, content, received_at = _extract_kf_content(msg)
            media_reference = _extract_kf_media_reference(msg)
            media_error_message = None
            if msgtype == "text":
                message_status = "pending"
            elif media_reference is not None and media_reference.supported:
                message_status = "pending_media"
            elif msgtype in {"image", "voice"}:
                message_status = "media_failed"
                media_error_message = f"WeCom {msgtype} message is missing media_id"
            else:
                message_status = "unsupported_media"
            store_result = await try_store_wecom_inbox(
                db,
                media_reference=media_reference,
                platform_id=platform_id,
                message_id=msgid,
                source_type="wecom_kf",  # WeCom Customer Service (客服)
                from_user=ext_uid or "",
                open_kfid=open_kf_id,
                msg_type=msgtype,
                content=content,
                is_from_colleague=False,
                raw_payload={"kf_sync_msg": msg},
                status=message_status,
                error_message=media_error_message,
                received_at=received_at,
            )
            if store_result == InboxStoreResult.ERROR:
                raise RuntimeError(f"Failed to persist WeCom KF message {msgid}")
            if store_result == InboxStoreResult.STORED:
                stored_count += 1

        # Update cursor and metrics
        cursor = data.get("next_cursor") or cursor
        if cursor:
            await persist_wecom_sync_cursor(
                db,
                platform_id,
                open_kf_id,
                corp_id,
                cursor,
                cursor_ttl_seconds,
            )

        has_more = int(data.get("has_more") or 0)
        logging.info(
            "[WECOM] KF sync page=%s fetched=%s stored=%s has_more=%s",
            page,
            len(msg_list),
            stored_count,
            has_more,
        )
        if not has_more:
            break
    if has_more:
        raise WeComSyncContinuation(
            "WeCom KF sync page budget exhausted; continue from durable cursor"
        )


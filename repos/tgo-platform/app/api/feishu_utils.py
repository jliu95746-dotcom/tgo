"""Feishu Bot API utilities.

This module provides helper functions for:
- Verifying Feishu webhook signatures
- Decrypting encrypted messages
- Getting tenant access tokens
- Replying to messages via the Reply API

Docs: https://open.feishu.cn/document/server-docs/im-v1/message/reply
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

from app.core.config import settings

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
    try:
        _redis_client = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True
        )
        await _redis_client.ping()
        return _redis_client
    except Exception as e:  # pragma: no cover
        logging.warning("[FEISHU] Redis unavailable: %s", type(e).__name__)
        return None


# --- Feishu signature verification -------------------------------------------------
def feishu_verify_signature(
    timestamp: str,
    nonce: str,
    encrypt_key: str,
    body: str,
    signature: str,
) -> bool:
    """Verify Feishu callback signature.

    Signature = sha256(timestamp + nonce + encrypt_key + body)

    Docs: https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case
    """
    if not signature:
        return False

    content = timestamp + nonce + encrypt_key + body
    computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return computed == signature


# --- Feishu message decryption -----------------------------------------------------
def feishu_decrypt_message(encrypt_b64: str, encrypt_key: str) -> Optional[str]:
    """Decrypt Feishu encrypted message using AES-256-CBC.

    The encrypt_key is used to derive a 32-byte AES key via SHA256.
    The first 16 bytes of the ciphertext are the IV.

    Docs: https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case

    Returns decrypted JSON string on success, None on failure.
    """
    try:
        # Derive AES key from encrypt_key using SHA256
        aes_key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()

        # Decode the base64 encrypted content
        ciphertext = base64.b64decode(encrypt_b64)

        # First 16 bytes are IV
        iv = ciphertext[:16]
        encrypted_data = ciphertext[16:]

        # Decrypt using AES-256-CBC
        cipher = Cipher(
            algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend()
        )
        decryptor = cipher.decryptor()
        padded_plain = decryptor.update(encrypted_data) + decryptor.finalize()

        # Remove PKCS7 padding
        pad_len = padded_plain[-1]
        if pad_len < 1 or pad_len > 16:
            return None
        plain = padded_plain[:-pad_len]

        return plain.decode("utf-8")
    except Exception as e:
        logging.error("[FEISHU] Decrypt failed: %s", type(e).__name__)
        return None


# --- Feishu tenant access token ----------------------------------------------------
_token_cache: Dict[str, tuple] = {}  # app_id -> (token, expires_at)


async def feishu_get_tenant_access_token(
    app_id: str,
    app_secret: str,
    timeout: Optional[int] = None,
) -> str:
    """Get Feishu tenant_access_token for API calls.

    Caches token in memory and Redis if available.

    Docs: https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
    """
    import time

    cache_key = f"feishu:tenant_token:{app_id}"
    now = time.time()

    # Check memory cache
    if app_id in _token_cache:
        token, expires_at = _token_cache[app_id]
        if expires_at > now + 60:  # 1 minute buffer
            return token

    # Check Redis cache
    redis = await get_redis_client()
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                data = json.loads(cached)
                if data.get("expires_at", 0) > now + 60:
                    _token_cache[app_id] = (data["token"], data["expires_at"])
                    return data["token"]
        except Exception as e:
            logging.warning("[FEISHU] Redis get failed: %s", type(e).__name__)

    # Fetch new token from Feishu API
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": app_id,
        "app_secret": app_secret,
    }

    async with httpx.AsyncClient(
        timeout=timeout or settings.request_timeout_seconds
    ) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError("Feishu get tenant_access_token failed")

        token = data["tenant_access_token"]
        expire = data.get("expire", 7200)  # Default 2 hours
        expires_at = now + expire

        # Cache in memory
        _token_cache[app_id] = (token, expires_at)

        # Cache in Redis
        if redis:
            try:
                await redis.setex(
                    cache_key,
                    expire - 60,  # Expire slightly earlier
                    json.dumps({"token": token, "expires_at": expires_at}),
                )
            except Exception as e:
                logging.warning("[FEISHU] Redis set failed: %s", type(e).__name__)

        return token


# --- Feishu Reply Message API ------------------------------------------------------
async def feishu_reply_message(
    access_token: str,
    message_id: str,
    msg_type: str,
    content: Dict[str, Any],
    timeout: Optional[int] = None,
) -> dict:
    """Reply to a Feishu message using the Reply API.

    Docs: https://open.feishu.cn/document/server-docs/im-v1/message/reply

    Args:
        access_token: Tenant access token
        message_id: The message_id to reply to (from callback event)
        msg_type: Message type (text, post, image, interactive, etc.)
        content: Message content dict

    Returns:
        API response dict

    Raises:
        RuntimeError if API returns error
    """
    url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reply"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }

    logging.debug("[FEISHU] Reply request started")

    async with httpx.AsyncClient(
        timeout=timeout or settings.request_timeout_seconds
    ) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError("Feishu reply message failed")

        logging.debug("[FEISHU] Reply request completed")
        return data


async def feishu_reply_text(
    access_token: str,
    message_id: str,
    text: str,
    timeout: Optional[int] = None,
) -> dict:
    """Convenience wrapper to reply with plain text.

    Args:
        access_token: Tenant access token
        message_id: The message_id to reply to
        text: Text content to send
    """
    return await feishu_reply_message(
        access_token=access_token,
        message_id=message_id,
        msg_type="text",
        content={"text": text},
        timeout=timeout,
    )


def feishu_clean_message_text(text: str) -> str:
    """Clean Feishu message text by removing @mentions.

    Feishu mentions in text messages typically look like:
    - "@_user_1 "
    - "@All "
    - "@UserNickname "

    This function removes these patterns from the beginning of the message
    or anywhere in the message to keep only the actual content.
    """
    if not text:
        return ""

    # Remove @mentions (e.g., @Name or @_user_1 followed by a space)
    # Use regex that matches @ at the start of string or after a space
    # to avoid accidentally matching email addresses.
    cleaned = re.sub(r"(^|\s)@[^\s]+\s*", r"\1", text)

    return cleaned.strip()


# --- Feishu Get User Info API -------------------------------------------------------
async def feishu_get_user_info(
    app_id: str,
    app_secret: str,
    open_id: str,
    timeout: Optional[int] = None,
) -> tuple[str | None, str | None]:
    """Get Feishu user's name and avatar by open_id.

    Note: This API requires `contact:user.base:readonly` permission.
    If the app doesn't have this permission, returns (None, None).

    Docs: https://open.feishu.cn/document/server-docs/contact-v3/user/get

    Args:
        app_id: Application ID
        app_secret: Application secret
        open_id: User's open_id

    Returns:
        Tuple of (name, avatar_url), either can be None if not found
    """
    if not (app_id and app_secret and open_id):
        logging.debug("[FEISHU] Missing required user info parameters")
        return None, None

    try:
        access_token = await feishu_get_tenant_access_token(app_id, app_secret, timeout)

        url = f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}"
        headers = {
            "Authorization": f"Bearer {access_token}",
        }
        params = {"user_id_type": "open_id"}

        async with httpx.AsyncClient(
            timeout=timeout or settings.request_timeout_seconds
        ) as client:
            resp = await client.get(url, headers=headers, params=params)

            # Handle non-2xx responses
            if resp.status_code != 200:
                try:
                    error_data = resp.json()
                    error_code = error_data.get("code")
                    # Common error codes:
                    # 99991672: No permission (contact:user.base:readonly required)
                    # 99991663: User not found
                    if error_code == 99991672:
                        logging.info("[FEISHU] No permission to get user info")
                    else:
                        logging.debug("[FEISHU] Get user info API returned an error")
                except Exception:
                    logging.debug(
                        "[FEISHU] Get user info failed with status %s", resp.status_code
                    )
                return None, None

            data = resp.json()

            if data.get("code") != 0:
                logging.debug("[FEISHU] Get user info API returned an error")
                return None, None

            user = data.get("data", {}).get("user", {})
            name = user.get("name") or user.get("en_name") or None
            avatar_url = (
                user.get("avatar", {}).get("avatar_origin")
                or user.get("avatar", {}).get("avatar_72")
                or None
            )

            logging.debug("[FEISHU] Got user info")
            return name, avatar_url

    except Exception as e:
        logging.debug("[FEISHU] Get user info failed: %s", type(e).__name__)
        return None, None


def feishu_extract_sender_info_from_event(
    event_data: dict,
) -> tuple[str | None, str | None]:
    """Extract sender name from Feishu event payload.

    The event may contain sender info depending on event type.
    This is a fallback when Contact API is not available.

    Args:
        event_data: The decrypted event data from callback

    Returns:
        Tuple of (name, avatar_url), either can be None
    """
    try:
        event = event_data.get("event") or {}
        sender = event.get("sender") or {}

        # Try to get sender_id info
        sender_id = sender.get("sender_id") or {}

        # The event might include sender_type and tenant_key
        # But typically doesn't include name directly

        # For mentions, check if there's mention info
        mentions = event.get("message", {}).get("mentions") or []
        for mention in mentions:
            if mention.get("id", {}).get("open_id") == sender_id.get("open_id"):
                return mention.get("name"), None

        return None, None
    except Exception:
        return None, None


# --- Feishu Send Message API (for proactive messages) ------------------------------
async def feishu_send_message(
    access_token: str,
    receive_id_type: str,
    receive_id: str,
    msg_type: str,
    content: Dict[str, Any],
    timeout: Optional[int] = None,
) -> dict:
    """Send a message to a user or chat.

    Docs: https://open.feishu.cn/document/server-docs/im-v1/message/create

    Args:
        access_token: Tenant access token
        receive_id_type: "open_id", "user_id", "union_id", or "chat_id"
        receive_id: The recipient ID
        msg_type: Message type
        content: Message content dict

    Returns:
        API response dict
    """
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    params = {"receive_id_type": receive_id_type}
    payload = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": json.dumps(content, ensure_ascii=False),
    }

    async with httpx.AsyncClient(
        timeout=timeout or settings.request_timeout_seconds
    ) as client:
        resp = await client.post(url, headers=headers, params=params, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(f"Feishu send message failed: {data}")

        return data

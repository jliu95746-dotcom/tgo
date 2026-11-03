"""DingTalk Bot API utilities.

This module provides helper functions for:
- Verifying DingTalk webhook signatures
- Sending messages via sessionWebhook

Docs: https://open.dingtalk.com/document/orgapp/receive-message
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from typing import Any, Awaitable, Dict, Optional

import httpx

from app.core.config import settings


async def _dingtalk_response(
    pending_response: Awaitable[httpx.Response],
) -> httpx.Response:
    """Preserve HTTP failures without retaining the signed session webhook."""
    status_code: int | None = None
    try:
        response = await pending_response
        response.raise_for_status()
        return response
    except httpx.HTTPStatusError as error:
        status_code = error.response.status_code
    except httpx.RequestError:
        pass

    # Raise outside the handler so the original token-bearing error is not chained.
    safe_request = httpx.Request("POST", "/")
    if status_code is not None:
        safe_response = httpx.Response(status_code, request=safe_request)
        raise httpx.HTTPStatusError(
            f"DingTalk webhook returned HTTP {status_code}",
            request=safe_request,
            response=safe_response,
        )
    raise httpx.RequestError("DingTalk webhook request failed", request=safe_request)


def dingtalk_compute_signature(timestamp: str, app_secret: str) -> str:
    """Compute DingTalk signature for callback verification.

    Signature = Base64(HMAC-SHA256(timestamp + "\\n" + app_secret, app_secret))

    Docs: https://open.dingtalk.com/document/orgapp/configure-event-subcription
    """
    string_to_sign = f"{timestamp}\n{app_secret}"
    hmac_code = hmac.new(
        app_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def dingtalk_verify_signature(
    timestamp: str,
    sign: str,
    app_secret: str,
) -> bool:
    """Verify DingTalk callback signature.

    The signature is computed as: Base64(HMAC-SHA256(timestamp + "\\n" + app_secret, app_secret))

    Args:
        timestamp: Timestamp from X-DingTalk-Timestamp header (milliseconds)
        sign: Signature from X-DingTalk-Sign header
        app_secret: App secret from platform config

    Returns:
        True if signature is valid, False otherwise
    """
    if not (timestamp and sign and app_secret):
        return False

    expected = dingtalk_compute_signature(timestamp, app_secret)
    return expected == sign


async def dingtalk_send_webhook(
    session_webhook: str,
    msgtype: str,
    content: Dict[str, Any],
    at: Optional[Dict[str, Any]] = None,
    timeout: Optional[int] = None,
) -> dict:
    """Send a message via DingTalk sessionWebhook.

    Docs: https://open.dingtalk.com/document/orgapp/the-robot-sends-a-group-message

    Args:
        session_webhook: The sessionWebhook URL from callback message
        msgtype: Message type (text, markdown, actionCard, feedCard)
        content: Message content dict
        at: Optional @mention configuration

    Returns:
        API response dict

    Raises:
        RuntimeError if API returns error
    """
    if not session_webhook:
        raise RuntimeError("DingTalk sessionWebhook is required")

    payload: Dict[str, Any] = {
        "msgtype": msgtype,
        msgtype: content,
    }

    if at:
        payload["at"] = at

    logging.info("[DINGTALK] Sending via session webhook")

    async with httpx.AsyncClient(timeout=timeout or settings.request_timeout_seconds) as client:
        resp = await _dingtalk_response(client.post(session_webhook, json=payload))
        data = resp.json()

        # DingTalk returns {"errcode": 0, "errmsg": "ok"} on success
        errcode = data.get("errcode")
        if errcode not in (0, None):
            raise RuntimeError("DingTalk send webhook failed")

        logging.info("[DINGTALK] Send success")
        return data


async def dingtalk_send_text(
    session_webhook: str,
    content: str,
    at_mobiles: Optional[list[str]] = None,
    at_user_ids: Optional[list[str]] = None,
    is_at_all: bool = False,
    timeout: Optional[int] = None,
) -> dict:
    """Convenience wrapper to send text message via DingTalk webhook.

    Args:
        session_webhook: The sessionWebhook URL
        content: Text content
        at_mobiles: List of mobile numbers to @mention
        at_user_ids: List of user IDs to @mention
        is_at_all: Whether to @all members
    """
    text_content = {"content": content}

    at_config = None
    if at_mobiles or at_user_ids or is_at_all:
        at_config = {
            "atMobiles": at_mobiles or [],
            "atUserIds": at_user_ids or [],
            "isAtAll": is_at_all,
        }

    return await dingtalk_send_webhook(
        session_webhook=session_webhook,
        msgtype="text",
        content=text_content,
        at=at_config,
        timeout=timeout,
    )


async def dingtalk_send_markdown(
    session_webhook: str,
    title: str,
    text: str,
    at_mobiles: Optional[list[str]] = None,
    at_user_ids: Optional[list[str]] = None,
    is_at_all: bool = False,
    timeout: Optional[int] = None,
) -> dict:
    """Convenience wrapper to send markdown message via DingTalk webhook.

    Args:
        session_webhook: The sessionWebhook URL
        title: Markdown title (shown in notification)
        text: Markdown content
        at_mobiles: List of mobile numbers to @mention
        at_user_ids: List of user IDs to @mention
        is_at_all: Whether to @all members
    """
    markdown_content = {
        "title": title,
        "text": text,
    }

    at_config = None
    if at_mobiles or at_user_ids or is_at_all:
        at_config = {
            "atMobiles": at_mobiles or [],
            "atUserIds": at_user_ids or [],
            "isAtAll": is_at_all,
        }

    return await dingtalk_send_webhook(
        session_webhook=session_webhook,
        msgtype="markdown",
        content=markdown_content,
        at=at_config,
        timeout=timeout,
    )


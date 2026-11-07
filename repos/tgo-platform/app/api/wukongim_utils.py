from __future__ import annotations

from typing import Any

import httpx


async def wukongim_send_message(
    platform_config: dict, to_uid: str, message_content: str
) -> dict[str, Any]:
    """Send a message to a WuKongIM user.

    Expected platform_config keys (one of):
    - wukongim_send_url: full URL to send endpoint
    - OR wukongim_base_url: base URL; we will POST to base + "/api/messages/send"
    - Optional: wukongim_api_token: Bearer token (Authorization header)
    """
    if not isinstance(platform_config, dict):
        raise RuntimeError("Invalid platform config for WuKongIM send")

    send_url = platform_config.get("wukongim_send_url")
    if not send_url:
        base = (platform_config.get("wukongim_base_url") or "").rstrip("/")
        if not base:
            raise RuntimeError("WuKongIM send URL not configured")
        send_url = f"{base}/api/messages/send"

    headers = {"Content-Type": "application/json"}
    token = platform_config.get("wukongim_api_token")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {"to_uid": to_uid, "content": message_content or ""}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(send_url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"WuKongIM send failed: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError("WuKongIM send invalid JSON response")

        # Accept common success shapes
        if isinstance(data, dict):
            if data.get("ok") is True:
                return data
            if data.get("code") in (0, "0"):
                return data
        raise RuntimeError(f"WuKongIM send error: {data}")

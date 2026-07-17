from __future__ import annotations

import hashlib
import logging
from typing import Optional


from app.domain.entities import StreamEvent
from app.domain.services.adapters.base import BasePlatformAdapter
from app.core.config import settings

from app.api.wecom_utils import (
    wecom_get_access_token,
    wecom_kf_send_msg,
    wecom_send_app_message,
)  # centralized API wrappers


def _truncate_utf8(text: str, max_bytes: int = 2048) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    logging.warning(
        "[WECOM] Reply exceeded %s UTF-8 bytes and was truncated",
        max_bytes,
    )
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


class WeComAdapter(BasePlatformAdapter):
    """Outbound adapter for WeCom (WeChat Work / 企业微信).

    - Non-streaming: sends the final aggregated content as a WeCom text message
    - Requires per-platform config: corp_id, agent_id, app_secret
    - Sends to the user specified by `to_user` (usually the inbound sender UserId)
    """

    supports_stream = False

    def __init__(
        self,
        corp_id: str,
        agent_id: str | None,
        app_secret: str,
        to_user: str,
        is_from_colleague: bool = True,
        open_kfid: Optional[str] | None = None,
        external_userid: Optional[str] | None = None,
        http_timeout: int | None = None,
        source_message_id: str | None = None,
    ) -> None:
        self.corp_id = corp_id
        self.agent_id = agent_id
        self.app_secret = app_secret
        self.to_user = to_user
        self.is_from_colleague = bool(is_from_colleague)
        self.open_kfid = open_kfid
        self.external_userid = external_userid
        self.http_timeout = http_timeout or settings.request_timeout_seconds
        self.source_message_id = source_message_id

    async def _get_access_token(self) -> str:
        return await wecom_get_access_token(self.corp_id, self.app_secret, timeout=self.http_timeout)

    async def send_incremental(self, ev: StreamEvent) -> None:  # pragma: no cover - not used
        # WeCom adapter does not support streaming output; ignore incremental events
        return

    async def send_final(self, content: dict) -> None:
        text = (content or {}).get("text") or ""
        if not text:
            # Nothing to send
            return

        access_token = await self._get_access_token()
        bounded_text = _truncate_utf8(text)

        if self.is_from_colleague:
            # Send via standard app message API to internal colleague (touser = UserID)
            if not self.agent_id:
                raise RuntimeError("WeCom colleague reply requires agent_id")
            await wecom_send_app_message(
                access_token=access_token,
                to_user=self.to_user,
                agent_id=self.agent_id,
                msgtype="text",
                content={"content": bounded_text},
                duplicate_check_interval=10,
                timeout=self.http_timeout,
            )
        else:
            # Send via KF customer service API to external user
            if not (self.open_kfid and (self.external_userid or self.to_user)):
                raise RuntimeError("WeCom KF send requires open_kfid and external_userid")
            ext_uid = self.external_userid or self.to_user
            reply_message_id = None
            if self.source_message_id:
                digest = hashlib.sha256(self.source_message_id.encode("utf-8")).hexdigest()
                reply_message_id = f"tgo_{digest[:28]}"
            await wecom_kf_send_msg(
                access_token=access_token,
                open_kfid=self.open_kfid,
                external_userid=ext_uid,
                msgtype="text",
                content={"content": bounded_text},
                message_id=reply_message_id,
            )



# WeCom API helpers moved to app/api/wecom_utils.py to centralize integration logic.

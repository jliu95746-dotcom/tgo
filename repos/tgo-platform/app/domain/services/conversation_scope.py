"""Opaque conversation and sender selectors, without forwarding channel secrets."""

import hashlib
from app.domain.entities import NormalizedMessage


def upstream_scope(message: NormalizedMessage) -> dict[str, str]:
    extra = message.extra or {}
    result = {}
    source = extra.get("message_id")
    if isinstance(source, str) and source:
        result["message_id"] = source
    for name in ("slack", "telegram", "feishu", "dingtalk", "wecom"):
        context = extra.get(name)
        if not isinstance(context, dict):
            continue
        conversation = (
            context.get("channel")
            or context.get("chat_id")
            or context.get("conversation_id")
            or context.get("open_kfid")
        )
        thread = context.get("conversation_thread", context.get("thread_ts")) or ""
        if conversation:
            value = f"{message.platform_id}:{name}:{conversation}:{thread}"
            result["reception_conversation"] = hashlib.sha256(
                value.encode()
            ).hexdigest()
            result["reception_author"] = hashlib.sha256(
                f"{message.platform_id}:{message.from_uid}".encode()
            ).hexdigest()
    return result

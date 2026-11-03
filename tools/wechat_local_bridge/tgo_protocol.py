"""Consume the existing authenticated TGO history protocol, without stream deltas."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BackendReply:
    key: str
    sequence: int
    text: str | None
    waiting: bool = False
    reason: str = ""


def object_map(value: object) -> Mapping[str, object]:
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return value
    return {}


def decode_reply(
    raw: Mapping[str, object], channel: str, visitor_uid: str
) -> BackendReply:
    if raw.get("channel_id") != channel or raw.get("channel_type") != 251:
        raise ValueError("客服消息不属于已绑定的测试会话")
    sequence = int(str(raw.get("message_seq", 0)))
    key = str(raw.get("client_msg_no") or raw.get("message_id") or "")
    if sequence <= 0 or not key:
        raise ValueError("客服消息缺少可靠编号")
    sender = str(raw.get("from_uid", ""))
    payload, meta = object_map(raw.get("payload")), object_map(raw.get("event_meta"))
    if payload.get("type") == 1004:
        return BackendReply(
            key, sequence, None, reason="客服系统已转人工，等待人工回复"
        )
    if isinstance(payload.get("type"), int) and 1000 <= payload["type"] < 2000:
        return BackendReply(key, sequence, None, reason="非客服回复")
    if sender == visitor_uid or not sender.endswith(("-staff", "-agent")):
        return BackendReply(key, sequence, None, reason="非客服回复")
    if raw.get("error"):
        return BackendReply(key, sequence, None, reason="回复失败，未转发")
    text: object = None
    if payload.get("type") == 1:
        text = payload.get("content")
    elif payload.get("type") == 100:
        if raw.get("end") != 1 and meta.get("completed") is not True:
            return BackendReply(key, sequence, None, waiting=True)
        events = meta.get("events", [])
        if isinstance(events, list):
            for item in events:
                event = object_map(item)
                snapshot = object_map(event.get("snapshot"))
                if event.get("event_key") == "main" and snapshot.get("kind") == "text":
                    if event.get("status") in ("error", "cancelled") or event.get(
                        "error"
                    ):
                        return BackendReply(
                            key, sequence, None, reason="回复未正常完成"
                        )
                    text = snapshot.get("text")
                    break
    if not isinstance(text, str) or not text.strip():
        return BackendReply(key, sequence, None, reason="无可转发的完整文字")
    text = " ".join(text.strip().splitlines())
    if len(text) > 1000:
        return BackendReply(
            key, sequence, None, reason="回复超过本轮 1000 字符限制，未发送"
        )
    return BackendReply(key, sequence, text)

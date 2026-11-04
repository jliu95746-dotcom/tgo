"""RPA reply filling only. Sending is deliberately absent from this module."""

from __future__ import annotations

from typing import NotRequired, Protocol, TypedDict


class RpaNotReady(RuntimeError):
    """Reply input has not started; a tracked local probe may need recovery."""


class RpaBackend(Protocol):
    def ensure_ready(self) -> None: ...
    def get_clipboard(self) -> str: ...
    def paste(self, text: str) -> None: ...
    def read_input(self) -> str: ...
    def restore_clipboard(self, text: str) -> None: ...
    def save_proof(self) -> None: ...


class RpaResult(TypedDict):
    chat_id: str
    input_readback_matches: bool
    characters: int
    sent: bool
    event_id: NotRequired[str]


def fill_draft(reply: str, backend: RpaBackend) -> RpaResult:
    if not reply.strip() or len(reply) > 1000 or "\n" in reply or "\r" in reply:
        raise ValueError("本轮草稿仅允许 1～1000 字符的单行文字")
    backend.ensure_ready()
    clipboard = backend.get_clipboard()
    try:
        try:
            backend.paste(reply)
            if backend.read_input() != reply:
                raise RuntimeError("输入框回读不匹配，结果未知，停止自动重试")
            backend.save_proof()
        except RpaNotReady as error:
            raise RuntimeError(
                "填写阶段前置条件变化，结果未知，停止自动重试"
            ) from error
        return {
            "chat_id": "filehelper",
            "input_readback_matches": True,
            "characters": len(reply),
            "sent": False,
        }
    finally:
        backend.restore_clipboard(clipboard)

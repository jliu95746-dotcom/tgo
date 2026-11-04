"""Forward authorized messages to TGO and deliver text replies through RPA."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Protocol

from .reader import Message, WeChatReader
from .media_pipeline import MediaPipeline
from .rpa import RpaBackend, RpaNotReady, fill_draft
from .tgo_client import TgoClient, TgoError, TgoUnavailable
from .tgo_protocol import decode_reply, object_map
from .tgo_store import TgoStore
from .watch import Watcher


class SendBackend(RpaBackend, Protocol):
    def send_point(self, expected: str) -> tuple[int, int]: ...
    def click_send(self, point: tuple[int, int], expected: str) -> None: ...


class TgoBridge(Watcher):
    def __init__(
        self,
        reader: WeChatReader,
        store: TgoStore,
        runtime: Path,
        prefix: str,
        client: TgoClient,
        backend: SendBackend | None,
        allow_send: bool,
        max_sends: int,
        max_seconds: float,
        *,
        scoped: bool = False,
        active: Callable[[], bool] | None = None,
        media: MediaPipeline | None = None,
    ):
        super().__init__(reader, store, runtime, prefix, backend, max_seconds)
        self.ledger, self.client, self.backend = store, client, backend
        self.allow_send, self.max_sends = allow_send, max_sends
        self.scoped, self.active = scoped, active or (lambda: True)
        self.media = media
        self.sent_this_run = 0
        self.next_delivery_at = 0.0
        self.next_heartbeat_at = 0.0
        self.rpa_status = "等待授权会话新消息" if scoped else "等待文件传输助手新消息"

    def initialize(self) -> None:
        self.client.connect()
        if self.ledger.value("tgo_channel") is None:
            history = self.client.history(0)
            sequence = max(
                (int(str(row.get("message_seq", 0))) for row in self._rows(history)),
                default=0,
            )
        else:
            sequence = self.ledger.sequence()
        identity = (
            f"{self.client.base}:{self.client.platform_id}:{self.client.channel_id}"
        )
        self.ledger.bind(identity, sequence)
        self.ledger.recover_interruption()
        if self.ledger.delivery_stats().get("uncertain"):
            raise TgoError("发现上次未确认的填写或发送，请先核对本机回执")
        super().initialize()

    @staticmethod
    def _rows(history: Mapping[str, object]) -> list[Mapping[str, object]]:
        rows = history.get("messages")
        if not isinstance(rows, list):
            raise TgoError("客服历史接口缺少消息列表")
        return [object_map(row) for row in rows]

    def _read_wechat(self) -> int:
        return self.observe(self.reader.new_messages(self.ledger.cursors()))

    def observe(self, messages: Iterable[Message]) -> int:
        count = 0
        for message in messages:
            if not self.active():
                break
            eligible = (
                ((message.message_type == 1 and bool(message.text.strip())
                  and len(message.text) <= 8000 and message.text.startswith(self.prefix))
                 or (self.scoped and self.media is not None and message.message_type in (3, 34)))
                and (not self.scoped or (
                    not message.is_self and bool(message.sender_id)
                    and (message.chat_id.endswith("@chatroom") or message.sender_id == message.chat_id)
                ))
            )
            count += self.ledger.observe(message, eligible=eligible)
        return count

    def _read_tgo(self) -> None:
        cursor = self.ledger.sequence()
        history = self.client.history(cursor)
        rows = sorted(
            self._rows(history), key=lambda row: int(str(row.get("message_seq", 0)))
        )
        for row in rows:
            reply = decode_reply(row, self.client.channel_id, self.client.channel_id)
            if reply.sequence <= cursor:
                continue
            if row.get("from_uid") == self.client.channel_id:
                payload = object_map(row.get("payload"))
                extra = object_map(payload.get("extra"))
                source_id = extra.get("message_id")
                if isinstance(source_id, str) and source_id.startswith("wxlocal:"):
                    self.ledger.accepted(
                        source_id.removeprefix("wxlocal:"), "tgo-history"
                    )
            if reply.waiting:
                break
            self.ledger.queue(reply)
            if reply.reason and reply.reason != "非客服回复":
                self.rpa_status = reply.reason

    def _wait_receipt(self, baseline: dict[str, int], text: str) -> Message | None:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            matches = [
                message
                for message in self.reader.history(30)
                if message.chat_id == self.ledger.chat_id
                and message.is_self
                and message.message_type == 1
                and message.text == text
                and message.local_id > baseline.get(message.shard, 0)
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                return None
            time.sleep(0.25)
        return None

    def _deliver(self) -> None:
        if self.stop.is_set() or not self.active() or time.monotonic() < self.next_delivery_at:
            return
        delivery = self.ledger.next_delivery()
        if not delivery:
            return
        if self.backend is None:
            self.rpa_status = (
                "微信窗口暂不可用，回复待人工处理" if self.scoped
                else "回复待人工核对：请先校准并打开当前会话"
            )
            return
        try:
            if delivery.state == "pending":
                try:
                    fill_draft(delivery.text, self.backend)
                except RpaNotReady:
                    raise
                except Exception as error:  # noqa: BLE001 - An interrupted paste needs manual reconciliation.
                    self.ledger.mark_fill_uncertain(delivery.key)
                    raise TgoError(
                        f"微信填写结果未知，已停止：{type(error).__name__}"
                    ) from None
                self.ledger.filled(delivery.key)
                self.rpa_status = "已填写并回读客服回复"
            if not self.allow_send or self.stop.is_set() or not self.active():
                return
            baseline = self.reader.baseline()
            point = self.backend.send_point(delivery.text)
            if self.stop.is_set() or not self.active():
                return
        except RpaNotReady as error:
            self.rpa_status = str(error)
            self.next_delivery_at = time.monotonic() + (25 if self.scoped else 8)
            return
        self.ledger.begin_send(delivery.key, baseline)
        receipt = None
        try:
            self.backend.click_send(point, delivery.text)
            receipt = self._wait_receipt(baseline, delivery.text)
        finally:
            self.ledger.resolve_send(delivery.key, receipt)
        if receipt is None:
            raise TgoError("微信未观察到唯一发送回执，已停止重复发送")
        self.sent_this_run += 1
        self.rpa_status = "微信已写入发送记录"
        (self.runtime / "tgo-delivery-proof.json").write_text(
            json.dumps(
                {
                    "chat_id": self.ledger.chat_id,
                    "tgo_reply_id": delivery.key,
                    "wechat_event_id": receipt.event_id,
                    "sent": True,
                    "characters": len(delivery.text),
                    "sent_this_run": self.sent_this_run,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if self.sent_this_run >= self.max_sends:
            self.stop.set()
            self.rpa_status = "已达到本轮试发上限"

    def poll(self, *, read_local: bool = True) -> int:
        with self.lock:
            if not self.active():
                return 0
            self.polls += 1
            count = self._read_wechat() if read_local else 0
            message = self.ledger.next_inbound()
            media = None
            if message and message.message_type in (3, 34):
                if self.media is None:
                    raise TgoError("当前会话尚未配置媒体读取")
                media = self.media.prepare(message)
                self.rpa_status = self.media.status
                if media is None:
                    message = None
            if message and not self.stop.is_set() and self.active():
                self.ledger.begin_submission(message.event_id)
                if media is not None and self.media is not None:
                    self.media.completed()
                if not self.active():
                    self.ledger.submission_uncertain(message.event_id)
                    return count
                try:
                    if self.scoped:
                        receipt = self.client.submit(
                            message.event_id, message.text,
                            mentioned=message.mentions_verified and self.reader.self_username in message.mentioned_ids,
                            mention_ambiguous=(
                                message.chat_id.endswith("@chatroom")
                                and ("@" in message.text or "＠" in message.text)
                                and (not message.mentions_verified or not message.mentioned_ids)
                            ),
                            author=hashlib.sha256(f"{self.client.account_tag}:{message.sender_id}".encode()).hexdigest(),
                            **({"media": media} if media is not None else {}),
                        )
                    else:
                        receipt = self.client.submit(message.event_id, message.text)
                except TgoUnavailable:
                    self.ledger.submission_uncertain(message.event_id)
                    self.rpa_status = "客服提交结果待查，保留消息并核对回执"
                except (TgoError, ValueError, OSError):
                    self.ledger.submission_uncertain(message.event_id)
                    raise
                else:
                    self.ledger.accepted(message.event_id, receipt)
                    if self.media is not None:
                        self.media.completed()
                    self.rpa_status = (
                        "客服系统已接收，等待人工回复"
                        if receipt
                        in ("human_handoff", "ai_disabled", "assist_mode", "queued", "received")
                        else "客服系统已接收，等待回复"
                    )
            if not self.stop.is_set() and self.active():
                self._read_tgo()
                now = time.monotonic()
                if not self.scoped and now >= self.next_heartbeat_at:
                    self.client.heartbeat()
                    self.next_heartbeat_at = now + 12
                self._deliver()
            return count

    def run(self) -> None:
        started = time.monotonic()
        failures = 0
        try:
            while (
                not self.stop.is_set() and time.monotonic() - started < self.max_seconds
            ):
                try:
                    self.poll()
                except TgoUnavailable as error:
                    self.last_error = str(error)
                    failures += 1
                    if failures >= 3:
                        break
                    self.stop.wait(3)
                else:
                    failures = 0
                    self.last_error = ""
                    self.stop.wait(1)
        except (TgoError, RpaNotReady, ValueError, OSError, sqlite3.Error) as error:
            self.last_error = str(error)
        except Exception as error:  # noqa: BLE001 - Stop on unknown desktop outcomes.
            self.last_error = f"联调已停止：{type(error).__name__}"
        finally:
            self.stop.set()

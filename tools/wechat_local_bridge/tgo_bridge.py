"""Forward authorized messages to TGO and deliver text replies through RPA."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import time
import threading
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from datetime import datetime
from typing import Protocol
from uuid import UUID

from .reader import Message, WeChatReader
from .media_pipeline import MediaPipeline
from .media_types import UploadedMedia
from .rpa import RpaBackend, RpaNotReady, fill_draft
from .tgo_client import TgoClient, TgoError, TgoUnavailable, TurnChanged
from .tgo_protocol import decode_reply, object_map
from .tgo_store import TgoStore
from .watch import Watcher


class SendBackend(RpaBackend, Protocol):
    def send_point(self, expected: str) -> tuple[int, int]: ...
    def click_send(self, point: tuple[int, int], expected: str) -> None: ...
    def clear_owned_draft(self, expected: str) -> None: ...


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
        self.delivery_failures = 0
        self.desktop_lock = threading.Lock()
        self.reply_ready_at: dict[str, float] = {}
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
        self._reconcile_receipts()
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
        if self.scoped:
            for item in self.client.staff_replies():
                delivery_id = str(UUID(str(item.get("delivery_id"))))
                key, text = item.get("client_msg_no"), item.get("text")
                if not isinstance(key, str) or not key or not isinstance(text, str) or not 0 < len(text.strip()) <= 8000:
                    raise TgoError("人工回复缺少可靠编号或有效正文")
                self.ledger.queue_staff(key=key, delivery_id=delivery_id, text=" ".join(text.strip().splitlines()))
            for item in self.client.turn_replies():
                key, turn_id = str(item["client_msg_no"]), str(item["turn_id"])
                version, text = int(str(item["version"])), str(item["text"])
                if isinstance(item.get("ready_at"), str):
                    try:
                        self.reply_ready_at[key] = datetime.fromisoformat(item["ready_at"].replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        pass
                if not 0 < len(text) <= 8000:
                    raise TgoError("客服回复过长，需要人工处理")
                self.ledger.queue_turn(key=key, turn_id=turn_id, version=version,
                                       text=" ".join(text.strip().splitlines()))
        cursor = self.ledger.sequence()
        history = self.client.history(cursor)
        rows = sorted(
            self._rows(history), key=lambda row: int(str(row.get("message_seq", 0)))
        )
        blocked = False
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
                blocked = True
                continue
            if blocked:
                # Inspect subsequent staff replies without losing the unfinished
                # anchor's position. Durable outbox keys deduplicate the replay.
                if reply.text:
                    self.ledger.queue_turn(key=reply.key, turn_id=None, version=None, text=reply.text)
            else:
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
        filled_here = False
        try:
            if delivery.staff_delivery_id:
                attempt = self.ledger.staff_attempt(delivery.key)
                self.client.staff_action(delivery.staff_delivery_id, "check", attempt_id=attempt)
            if delivery.turn_id:
                self.client.turn_action(delivery.turn_id, delivery.version, "check")
            if delivery.state == "pending":
                try:
                    with self.desktop_lock:
                        fill_draft(delivery.text, self.backend)
                except RpaNotReady:
                    raise
                except Exception as error:  # noqa: BLE001 - An interrupted paste needs manual reconciliation.
                    self.ledger.mark_fill_uncertain(delivery.key)
                    raise TgoError(
                        f"微信填写结果未知，已停止：{type(error).__name__}"
                    ) from None
                self.ledger.filled(delivery.key)
                filled_here = True
                self.rpa_status = "已填写并回读客服回复"
            if not self.allow_send or self.stop.is_set() or not self.active():
                return
            with self.desktop_lock:
                baseline = self.reader.baseline()
                point = self.backend.send_point(delivery.text)
            if self.stop.is_set() or not self.active():
                return
            if delivery.turn_id:
                claim = self.client.turn_action(delivery.turn_id, delivery.version, "claim")
                self.ledger.save_claim(delivery.key, str(claim["claim_id"]))
            elif delivery.staff_delivery_id:
                claim = self.client.staff_action(delivery.staff_delivery_id, "claim", attempt_id=attempt)
                self.ledger.save_claim(delivery.key, str(claim["claim_id"]))
        except TurnChanged:
            if delivery.state == "filled" or filled_here:
                try:
                    with self.desktop_lock:
                        self.backend.clear_owned_draft(delivery.text)
                except (RpaNotReady, RuntimeError):
                    self.rpa_status = "旧回复已停止发送，人工草稿保留"
            self.ledger.supersede(delivery.key)
            self.rpa_status = ("会话归属或发送许可已变化，人工回复已停止发送"
                               if delivery.staff_delivery_id else "客户已有补充，旧回复已停止发送")
            return
        except RpaNotReady as error:
            self.rpa_status = str(error)
            self.delivery_failures += 1
            self.next_delivery_at = time.monotonic() + min(5, 2 ** (self.delivery_failures - 1))
            return
        self.ledger.begin_send(delivery.key, baseline)
        receipt = None
        try:
            with self.desktop_lock:
                point = self.backend.send_point(delivery.text)
                clicked_at = time.time()
                self.backend.click_send(point, delivery.text)
            receipt = self._wait_receipt(baseline, delivery.text)
        finally:
            self.ledger.resolve_send(delivery.key, receipt)
            if delivery.staff_delivery_id:
                try:
                    self._report_staff_receipts()
                except (TgoError, OSError):
                    pass  # The durable receipt is retried without repeating the click.
        if receipt is None:
            raise TgoError("微信未观察到唯一发送回执，已停止重复发送")
        self.delivery_failures = 0
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
                    "ready_to_click_seconds": round(clicked_at - self.reply_ready_at[delivery.key], 3)
                        if delivery.key in self.reply_ready_at else None,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if self.sent_this_run >= self.max_sends:
            self.stop.set()
            self.rpa_status = "已达到本轮试发上限"

    def _submit(self, message: Message, *, media: UploadedMedia | None = None,
                pending_kind: str | None = None, media_error: str | None = None) -> str:
        if self.scoped:
            return self.client.submit(
                message.event_id, message.text,
                mentioned=message.mentions_verified and self.reader.self_username in message.mentioned_ids,
                mention_ambiguous=(message.chat_id.endswith("@chatroom")
                    and ("@" in message.text or "＠" in message.text)
                    and (not message.mentions_verified or not message.mentioned_ids)),
                author=hashlib.sha256(f"{self.client.account_tag}:{message.sender_id}".encode()).hexdigest(),
                **({"media": media} if media is not None else {}),
                **({"pending_kind": pending_kind} if pending_kind is not None else {}),
                **({"media_error": media_error} if media_error is not None else {}))
        return self.client.submit(message.event_id, message.text)

    def _report_receipts(self) -> None:
        self._report_staff_receipts()
        for key, turn_id, version, claim_id, state, proof in self.ledger.pending_receipts():
            self.client.turn_action(turn_id, version, "receipt", claim_id=claim_id,
                result="sent" if state == "sent" else "unknown", receipt=proof)
            self.ledger.receipt_accepted(key)

    def _report_staff_receipts(self) -> None:
        for key, delivery_id, claim_id, state, proof in self.ledger.pending_staff_receipts():
            result = self.client.staff_action(delivery_id, "receipt", claim_id=claim_id,
                result="sent" if state == "sent" else "unknown", receipt=proof)
            if result.get("accepted") is not True:
                raise TgoError("云端未确认人工回复发送回执")
            self.ledger.receipt_accepted(key)

    def _reconcile_receipts(self) -> None:
        """Read only: a unique persisted WeChat row can finish a delayed receipt."""
        for key, text, snapshot, attempted_at in self.ledger.uncertain_sends():
            baseline = json.loads(snapshot)
            matches = [message for message in self.reader.history(100)
                if message.chat_id == self.ledger.chat_id and message.is_self and message.message_type == 1
                and message.text == text and message.local_id > baseline.get(message.shard, 0)
                and attempted_at - 2 <= message.created_at <= attempted_at + 120]
            if len(matches) == 1:
                self.ledger.reconcile_sent(key, matches[0].event_id)

    def poll(self, *, read_local: bool = True, deliver: bool = True) -> int:
        with self.lock:
            if not self.active():
                return 0
            self.polls += 1
            count = self._read_wechat() if read_local else 0
            message = self.ledger.next_inbound()
            # Register every observed part before starting slow upload/recognition.
            # Media placeholders must not block later text in the same question.
            submitted = 0
            while message and not self.stop.is_set() and self.active() and submitted < 100:
                self.ledger.begin_submission(message.event_id)
                if not self.active():
                    self.ledger.submission_uncertain(message.event_id)
                    return count
                try:
                    kind = ("image" if message.message_type == 3 else "voice") if message.message_type in (3, 34) else None
                    receipt = self._submit(message, pending_kind=kind)
                except TgoUnavailable:
                    self.ledger.submission_uncertain(message.event_id)
                    self.rpa_status = "客服提交结果待查，保留消息并核对回执"
                    break
                except (TgoError, ValueError, OSError):
                    self.ledger.submission_uncertain(message.event_id)
                    raise
                else:
                    if kind:
                        self.ledger.placeholder(message.event_id)
                    else:
                        self.ledger.accepted(message.event_id, receipt)
                    self.rpa_status = (
                        "客服系统已接收，等待人工回复"
                        if receipt
                        in ("human_handoff", "ai_disabled", "assist_mode", "queued", "received")
                        else "客服系统已接收，等待回复"
                    )
                submitted += 1
                message = self.ledger.next_inbound()
            pending = self.ledger.media_pending()
            if pending and self.media is not None:
                media = self.media.prepare(pending)
                if media is not None:
                    receipt = self._submit(pending, media=media)
                    self.ledger.accepted(pending.event_id, receipt)
                    self.media.completed()
                elif getattr(self.media, "failed", False) is True:
                    kind = "image" if pending.message_type == 3 else "voice"
                    receipt = self._submit(pending, pending_kind=kind, media_error="source_unavailable")
                    self.ledger.accepted(pending.event_id, receipt)
                    self.media.completed()
            if not self.stop.is_set() and self.active():
                if self.scoped:
                    self._report_receipts()
                self._read_tgo()
                now = time.monotonic()
                if not self.scoped and now >= self.next_heartbeat_at:
                    self.client.heartbeat()
                    self.next_heartbeat_at = now + 12
                if deliver:
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

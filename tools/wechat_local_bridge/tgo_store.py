"""Local delivery ledger for the one permitted WeChat test conversation."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .reader import Message
from .store import InboxStore
from .tgo_protocol import BackendReply


@dataclass(frozen=True)
class Delivery:
    key: str
    text: str
    state: str


class TgoStore(InboxStore):
    def __init__(self, path: Path, chat_id: str = "filehelper"):
        super().__init__(path)
        self.chat_id = chat_id
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS outbox (
                key TEXT PRIMARY KEY, sequence INTEGER NOT NULL, text TEXT NOT NULL,
                state TEXT NOT NULL, wx_baseline TEXT, attempted_at INTEGER, wx_event_id TEXT
            )
        """)

    def value(self, name: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE name=?", (name,)
        ).fetchone()
        return row[0] if row else None

    def _value(self, name: str, value: str) -> None:
        self.connection.execute(
            "INSERT INTO metadata VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
            (name, value),
        )

    def bind(self, identity: str, sequence: int) -> None:
        previous = self.value("tgo_channel")
        if previous is not None and previous != identity:
            raise ValueError("本机消息账本已绑定另一客服会话")
        if previous is None:
            with self.connection:
                self._value("tgo_channel", identity)
                self._value("tgo_sequence", str(sequence))

    def sequence(self) -> int:
        return int(self.value("tgo_sequence") or 0)

    def observe(self, message: Message, *, eligible: bool) -> bool:
        if message.chat_id != self.chat_id:
            raise ValueError("消息不属于当前授权会话")
        echo = self.is_echo(message)
        state = "echo" if echo else "observed" if eligible else "ignored"
        with self.connection:
            inserted = self.connection.execute(
                "INSERT OR IGNORE INTO inbox VALUES (?,?,?,?)",
                (
                    message.event_id,
                    json.dumps(vars(message), ensure_ascii=False),
                    "",
                    state,
                ),
            ).rowcount
            self.connection.execute(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(source) DO UPDATE SET last_id=MAX(last_id,excluded.last_id)",
                (message.shard, message.local_id),
            )
        return bool(inserted and state == "observed")

    def next_inbound(self) -> Message | None:
        row = self.connection.execute(
            "SELECT payload FROM inbox WHERE status='observed' ORDER BY rowid LIMIT 1"
        ).fetchone()
        return Message(**json.loads(row[0])) if row else None

    def begin_submission(self, event_id: str) -> None:
        with self.connection:
            updated = self.connection.execute(
                "UPDATE inbox SET status='submitting' WHERE event_id=? AND status='observed'",
                (event_id,),
            ).rowcount
        if updated != 1:
            raise ValueError("该微信消息已进入客服提交流程")

    def submission_uncertain(self, event_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inbox SET status='submit_uncertain' WHERE event_id=? AND status='submitting'",
                (event_id,),
            )

    def accepted(self, event_id: str, receipt: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inbox SET status='accepted',reply=? WHERE event_id=? AND status IN ('submitting','submit_uncertain')",
                (receipt, event_id),
            )

    def queue(self, reply: BackendReply) -> None:
        if reply.waiting:
            raise ValueError("回复尚未完成")
        with self.connection:
            if reply.text:
                previous = self.connection.execute(
                    "SELECT text FROM outbox WHERE key=?", (reply.key,)
                ).fetchone()
                if previous and previous[0] != reply.text:
                    raise ValueError("客服回复编号重复且正文不一致")
                self.connection.execute(
                    "INSERT OR IGNORE INTO outbox(key,sequence,text,state) VALUES (?,?,?,'pending')",
                    (reply.key, reply.sequence, reply.text),
                )
            self._value("tgo_sequence", str(max(self.sequence(), reply.sequence)))

    def next_delivery(self) -> Delivery | None:
        row = self.connection.execute(
            "SELECT key,text,state FROM outbox WHERE state IN ('pending','filled') ORDER BY sequence LIMIT 1"
        ).fetchone()
        return Delivery(*row) if row else None

    def filled(self, key: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE outbox SET state='filled' WHERE key=? AND state='pending'",
                (key,),
            )

    def begin_send(self, key: str, baseline: dict[str, int]) -> None:
        with self.connection:
            updated = self.connection.execute(
                "UPDATE outbox SET state='sending',wx_baseline=?,attempted_at=? WHERE key=? AND state='filled'",
                (json.dumps(baseline), int(time.time()), key),
            ).rowcount
            if updated != 1:
                raise ValueError("客服回复已进入发送流程，停止重复点击")

    def resolve_send(self, key: str, message: Message | None) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE outbox SET state=?,wx_event_id=? WHERE key=? AND state='sending'",
                (
                    "sent" if message else "uncertain",
                    message.event_id if message else None,
                    key,
                ),
            )

    def mark_fill_uncertain(self, key: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE outbox SET state='uncertain' WHERE key=? AND state='pending'",
                (key,),
            )

    def recover_interruption(self) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inbox SET status='submit_uncertain' WHERE status='submitting'"
            )
            self.connection.execute(
                "UPDATE outbox SET state='uncertain' WHERE state='sending'"
            )

    def is_echo(self, message: Message) -> bool:
        if (
            message.chat_id != self.chat_id
            or not message.is_self
            or message.message_type != 1
        ):
            return False
        # WeChat first writes an outgoing row with server_id=0, then assigns its ID.
        provisional_id = replace(message, server_id="0").event_id
        for event_id, snapshot, attempted_at in self.connection.execute(
            "SELECT wx_event_id,wx_baseline,attempted_at FROM outbox WHERE text=? AND state IN ('sending','sent','uncertain')",
            (message.text,),
        ):
            if event_id in (message.event_id, provisional_id):
                return True
            if not event_id and snapshot and attempted_at:
                baseline = json.loads(snapshot)
                if (
                    message.local_id > baseline.get(message.shard, 0)
                    and attempted_at - 2 <= message.created_at <= attempted_at + 120
                ):
                    return True
        return False

    def delivery_stats(self) -> dict[str, int]:
        return dict(
            self.connection.execute("SELECT state,COUNT(*) FROM outbox GROUP BY state")
        )

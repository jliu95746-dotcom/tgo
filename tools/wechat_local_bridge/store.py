"""Durable local inbox. Source messages and generated drafts remain on this computer."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

from .reader import Message


class PendingDraft(TypedDict):
    event_id: str
    reply: str
    chat_id: str


class InboxStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS cursors(source TEXT PRIMARY KEY,last_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(event_id TEXT PRIMARY KEY,payload TEXT NOT NULL,reply TEXT NOT NULL,status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)

    def initialized(self) -> bool:
        return bool(
            self.connection.execute(
                "SELECT 1 FROM metadata WHERE name='baseline'"
            ).fetchone()
        )

    def baseline(self, maxima: dict[str, int]) -> None:
        if self.initialized():
            return
        with self.connection:
            self.connection.executemany(
                "INSERT INTO cursors VALUES (?,?)", maxima.items()
            )
            self.connection.execute("INSERT INTO metadata VALUES ('baseline','ready')")

    def cursors(self) -> dict[str, int]:
        return dict(self.connection.execute("SELECT source,last_id FROM cursors"))

    def ingest(self, message: Message, reply: str) -> bool:
        if message.chat_id != "filehelper":
            raise ValueError("本轮仅授权文件传输助手")
        with self.connection:
            inserted = self.connection.execute(
                "INSERT OR IGNORE INTO inbox VALUES (?,?,?,?)",
                (
                    message.event_id,
                    json.dumps(asdict(message), ensure_ascii=False),
                    reply,
                    "pending" if reply else "observed",
                ),
            ).rowcount
            self.connection.execute(
                "INSERT INTO cursors VALUES (?,?) ON CONFLICT(source) DO UPDATE SET last_id=MAX(last_id,excluded.last_id)",
                (message.shard, message.local_id),
            )
        return bool(inserted)

    def pending_draft(self) -> PendingDraft | None:
        row = self.connection.execute(
            "SELECT event_id,reply FROM inbox WHERE status='pending' ORDER BY rowid LIMIT 1"
        ).fetchone()
        return (
            {"event_id": row[0], "reply": row[1], "chat_id": "filehelper"}
            if row
            else None
        )

    def latest_filled(self) -> PendingDraft | None:
        row = self.connection.execute(
            "SELECT event_id,reply FROM inbox WHERE status='filled' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return (
            {"event_id": row[0], "reply": row[1], "chat_id": "filehelper"}
            if row
            else None
        )

    def acknowledge(self, event_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inbox SET status='filled' WHERE event_id=? AND status='pending'",
                (event_id,),
            )

    def prepare_reply(self, event_id: str, reply: str) -> None:
        with self.connection:
            updated = self.connection.execute(
                "UPDATE inbox SET reply=?,status='pending' WHERE event_id=? AND status='observed'",
                (reply, event_id),
            ).rowcount
            if not updated:
                raise ValueError("该消息不存在或已处理，不能重复准备草稿")

    def uncertain(self, event_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inbox SET status='uncertain' WHERE event_id=? AND status='pending'",
                (event_id,),
            )

    def stats(self) -> dict[str, int]:
        return dict(
            self.connection.execute("SELECT status,COUNT(*) FROM inbox GROUP BY status")
        )

    def close(self) -> None:
        self.connection.close()

"""Read only the explicitly allowed conversation from verified local databases."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

import zstandard

from .crypto import open_snapshot


def conversation_table(username: str) -> str:
    if not username or len(username) > 256:
        raise ValueError("会话标识无效")
    return "Msg_" + hashlib.md5(username.encode()).hexdigest()


def decode_content(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if value.startswith(b"\x28\xb5\x2f\xfd"):
        value = zstandard.ZstdDecompressor().decompress(
            value, max_output_size=4 * 1024**2
        )
    return value.decode("utf-8", errors="replace")


@dataclass(frozen=True)
class Message:
    shard: str
    chat_id: str
    local_id: int
    server_id: str
    sort_seq: int
    created_at: int
    message_type: int
    sender_id: str
    is_self: bool
    text: str
    mentioned_ids: tuple[str, ...] = ()
    mentions_verified: bool = False

    @property
    def event_id(self) -> str:
        identity = f"{self.shard}:{self.chat_id}:{self.local_id}:{self.server_id}"
        return hashlib.sha256(identity.encode()).hexdigest()


def structured_mentions(value: bytes | str | None) -> tuple[tuple[str, ...], bool]:
    if value is None or value == b"" or value == "":
        return (), True
    try:
        text = decode_content(value)
        if len(text) > 16384 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.IGNORECASE):
            return (), False
        root = ET.fromstring(text)
        if root.tag != "msgsource":
            return (), False
        lists = list(root.iter("atuserlist"))
        if len(lists) > 1:
            return (), False
        values = tuple(item.strip() for item in (lists[0].text or "").split(",") if item.strip()) if lists else ()
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item) for item in values):
            return (), False
        return values, True
    except (ET.ParseError, ValueError, zstandard.ZstdError):
        return (), False


def read_messages(
    connection: sqlite3.Connection,
    shard: str,
    chat_id: str,
    self_username: str,
    after: int,
    limit: int,
    descending: bool = False,
) -> list[Message]:
    table = conversation_table(chat_id)
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone():
        return []
    order = "DESC" if descending else "ASC"
    rows = connection.execute(
        f'SELECT * FROM "{table}" WHERE local_id>? ORDER BY local_id {order} LIMIT ?',
        (after, limit),
    ).fetchall()
    result = []
    names: dict[int, str] = {}
    for row in rows:
        sender_number = int(row["real_sender_id"])
        if sender_number not in names:
            sender = connection.execute(
                "SELECT user_name FROM Name2Id WHERE rowid=?", (sender_number,)
            ).fetchone()
            names[sender_number] = str(sender[0]) if sender else ""
        text = decode_content(row["message_content"]) or decode_content(
            row["compress_content"]
        )
        mentions, verified = (), False
        if chat_id.endswith("@chatroom"):
            if "source" in row.keys():
                mentions, verified = structured_mentions(row["source"])
            if names[sender_number]:
                prefix = names[sender_number] + ":\n"
                if text.startswith(prefix):
                    text = text[len(prefix):]
        result.append(
            Message(
                shard,
                chat_id,
                int(row["local_id"]),
                str(row["server_id"]),
                int(row["sort_seq"]),
                int(row["create_time"]),
                int(row["local_type"]) & 0xFFFFFFFF,
                names[sender_number],
                names[sender_number] == self_username,
                text,
                mentions,
                verified,
            )
        )
    return result


@dataclass(frozen=True)
class DatabaseKey:
    path: Path
    key: bytes = field(repr=False)


@dataclass(frozen=True)
class SessionUnread:
    count: int
    last_cleared: int
    last_message: int


class WeChatReader:
    def __init__(self, key_file: Path, chat_id: str = "filehelper", *, scoped: bool = False):
        if chat_id != "filehelper" and not scoped:
            raise ValueError("本轮仅授权文件传输助手")
        data = json.loads(key_file.read_text(encoding="utf-8"))
        self.databases = [
            DatabaseKey(Path(item["path"]).resolve(), bytes.fromhex(item["key"]))
            for item in data["databases"]
        ]
        roots = {
            next(p for p in entry.path.parents if p.name == "db_storage")
            for entry in self.databases
        }
        if len(roots) != 1:
            raise ValueError("密钥未唯一匹配当前微信账号，停止自动选择")
        self.root = roots.pop()
        self.self_username = re.sub(r"_[0-9a-fA-F]{4}$", "", self.root.parent.name)
        self.chat_id = chat_id
        self.shards = [
            entry
            for entry in self.databases
            if re.fullmatch(r"message_\d+\.db", entry.path.name)
        ]
        self._signatures: dict[str, tuple[tuple[int, int], ...]] = {}

    def shard_name(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _signature(self, path: Path) -> tuple[tuple[int, int], ...]:
        files = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
        return tuple(
            (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else (0, 0)
            for p in files
        )

    def baseline(self) -> dict[str, int]:
        maxima = {}
        table = conversation_table(self.chat_id)
        for entry in self.shards:
            connection = open_snapshot(entry.path, entry.key)
            try:
                name = self.shard_name(entry.path)
                found = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                ).fetchone()
                maxima[name] = (
                    int(
                        connection.execute(
                            f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"'
                        ).fetchone()[0]
                    )
                    if found
                    else 0
                )
                # Do not cache a post-query signature: a message could arrive between the read and stat.
            finally:
                connection.close()
        return maxima

    def history(self, limit: int = 20) -> list[Message]:
        result = []
        for entry in self.shards:
            connection = open_snapshot(entry.path, entry.key)
            try:
                result.extend(
                    read_messages(
                        connection,
                        self.shard_name(entry.path),
                        self.chat_id,
                        self.self_username,
                        0,
                        limit,
                        True,
                    )
                )
            finally:
                connection.close()
        return sorted(
            result,
            key=lambda message: (
                message.created_at,
                message.sort_seq,
                message.local_id,
            ),
        )[-limit:]

    def new_messages(self, cursors: dict[str, int], limit: int = 100) -> list[Message]:
        known = {entry.path for entry in self.shards}
        found = {
            path.resolve()
            for path in self.root.rglob("message_*.db")
            if re.fullmatch(r"message_\d+\.db", path.name)
        }
        if found - known:
            raise ValueError("出现尚无密钥的新消息分库，请重新执行只读取钥")
        result = []
        for entry in self.shards:
            name = self.shard_name(entry.path)
            signature = self._signature(entry.path)
            if self._signatures.get(name) == signature:
                continue
            connection = open_snapshot(entry.path, entry.key)
            try:
                table = conversation_table(self.chat_id)
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
                ).fetchone():
                    maximum = int(
                        connection.execute(
                            f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"'
                        ).fetchone()[0]
                    )
                    if maximum < cursors.get(name, 0):
                        raise ValueError(
                            "消息分库序号回退，需要人工核对基线，停止重放历史"
                        )
                batch = read_messages(
                    connection,
                    name,
                    self.chat_id,
                    self.self_username,
                    cursors.get(name, 0),
                    limit,
                )
                result.extend(batch)
            finally:
                connection.close()
            # A full batch may leave more messages. Query it again on the next poll.
            if len(batch) < limit:
                self._signatures[name] = signature
        return sorted(
            result,
            key=lambda message: (
                message.created_at,
                message.sort_seq,
                message.local_id,
            ),
        )

    def session_unread(self) -> SessionUnread | None:
        entry = next(item for item in self.databases if item.path.name == "session.db")
        connection = open_snapshot(entry.path, entry.key)
        try:
            row = connection.execute(
                "SELECT unread_count, last_clear_unread_timestamp, last_timestamp FROM SessionTable WHERE username=?",
                (self.chat_id,),
            ).fetchone()
            return SessionUnread(*row) if row and all(type(value) is int for value in row) else None
        finally:
            connection.close()

    def draft_matches(self, expected: str) -> bool:
        entry = next(item for item in self.databases if item.path.name == "session.db")
        connection = open_snapshot(entry.path, entry.key)
        try:
            row = connection.execute(
                "SELECT draft FROM SessionTable WHERE username=?", (self.chat_id,)
            ).fetchone()
            if row and expected in decode_content(row[0]):
                return True
            rows = connection.execute(
                "SELECT draft_data FROM SessionDraft WHERE username=?", (self.chat_id,)
            ).fetchall()
            return any(
                expected.encode()
                in (row[0] if isinstance(row[0], bytes) else str(row[0]).encode())
                for row in rows
            )
        finally:
            connection.close()

    def export_history(self, destination: Path, limit: int = 20) -> int:
        messages = self.history(limit)
        destination.write_text(
            json.dumps(
                [asdict(message) for message in messages], ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        return len(messages)

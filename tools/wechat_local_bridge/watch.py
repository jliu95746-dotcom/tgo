"""Poll committed local message updates; draft only in the allowed test conversation."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .reader import WeChatReader
from .rpa import RpaBackend, RpaNotReady, fill_draft
from .store import InboxStore


class Watcher:
    def __init__(
        self,
        reader: WeChatReader,
        store: InboxStore,
        runtime: Path,
        prefix: str,
        rpa: RpaBackend | None = None,
        max_seconds: float = 1800,
    ):
        self.reader = reader
        self.store = store
        self.runtime = runtime
        self.prefix = prefix
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.last_error = ""
        self.polls = 0
        self.last_seen = 0
        self.rpa = rpa
        self.rpa_status = "仅生成草稿"
        self.max_seconds = max_seconds

    def initialize(self) -> None:
        with self.lock:
            if not self.store.initialized():
                self.store.baseline(self.reader.baseline())

    def poll(self) -> int:
        with self.lock:
            messages = self.reader.new_messages(self.store.cursors())
            count = 0
            for message in messages:
                reply = ""
                if message.message_type == 1 and message.text.startswith(self.prefix):
                    reply = "已收到你的测试消息，本机读取与 RPA 填写回复链路已连通。"
                count += self.store.ingest(message, reply)
            self.polls += 1
            if count:
                self.last_seen = int(time.time())
            draft = self.store.pending_draft()
            if draft:
                target = self.runtime / "draft.private.json"
                temp = target.with_suffix(".tmp")
                temp.write_text(
                    json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temp.replace(target)
                if self.rpa:
                    try:
                        report = fill_draft(draft["reply"], self.rpa)
                    except RpaNotReady as error:
                        self.rpa_status = str(error)
                    except Exception as error:  # noqa: BLE001 - Unknown UI outcomes must stop retries.
                        self.store.uncertain(draft["event_id"])
                        self.rpa_status = f"RPA 结果未知，已停止该条自动重试：{type(error).__name__}: {error}"
                    else:
                        report["event_id"] = draft["event_id"]
                        (self.runtime / "rpa-verification.json").write_text(
                            json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                        self.store.acknowledge(draft["event_id"])
                        self.rpa_status = "已填写并回读核验，未发送"
            return count

    def run(self) -> None:
        started = time.monotonic()
        while not self.stop.is_set():
            if time.monotonic() - started >= self.max_seconds:
                self.stop.set()
                break
            try:
                count = self.poll()
                self.last_error = ""
                if count:
                    print(
                        json.dumps(
                            {
                                "event": "new_messages",
                                "count": count,
                                "draft_ready": bool(self.store.pending_draft()),
                            }
                        ),
                        flush=True,
                    )
            except (ValueError, OSError, sqlite3.Error) as error:
                self.last_error = str(error)
                print(
                    json.dumps({"event": "retry", "error_type": type(error).__name__}),
                    flush=True,
                )
            self.stop.wait(1)

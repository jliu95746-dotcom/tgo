"""Run independent ledgers for locally authorized customer conversations."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .crypto import open_snapshot
from .reader import WeChatReader, conversation_table, read_messages
from .reception_scope import Grant, Scope
from .rpa import RpaNotReady
from .tgo_bridge import TgoBridge
from .tgo_client import TgoClient, TgoError, TgoUnavailable, visitor_token_expired
from .tgo_store import TgoStore
from .window_control import WindowControl


def cloud_identity(account_tag: str, grant: Grant) -> str:
    kind = "group" if grant.contact.is_group else "private"
    opaque = hashlib.sha256(f"{grant.revision}:{grant.contact.id}".encode()).hexdigest()
    return f"wechat-local:{account_tag}:{kind}:{opaque}"


@dataclass
class Worker:
    grant: Grant
    bridge: TgoBridge
    connected: bool = False
    next_poll: float = 0
    error: str = ""
    target_title: str = ""


class ScopedBridge:
    def __init__(self, scope: Scope, client: TgoClient, runtime: Path, allow_send: bool,
                 window: WindowControl | None = None):
        self.scope, self.client, self.runtime = scope, client, runtime
        self.allow_send = allow_send
        self.window = window
        self.window_ready = False
        self.key_file = runtime / "keys.private.json"
        self.workers: dict[str, Worker] = {}
        self.signatures: dict[str, object] = {}
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.desktop_lock = threading.Lock()
        self.healthy = False
        self.error = ""
        self.status_snapshot: dict[str, dict[str, object]] = {}
        self.next_publish = 0.0
        self.published: list[Mapping[str, object]] | None = None

    def target_runtime(self, grant: Grant) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", grant.revision):
            raise ValueError("本机会话授权编号无效")
        identity = hashlib.sha256(grant.contact.id.encode()).hexdigest()
        key = hashlib.sha256(f"{grant.revision}:{identity}".encode()).hexdigest()[:32]
        targets = self.runtime / "targets"
        path = targets / key
        legacy = targets / identity / grant.revision
        if legacy.is_dir():
            if path.exists():
                raise ValueError("本机会话有两份记录，已暂停以避免重复处理")
            legacy.rename(path)
        return path

    def authorize(self, grant: Grant, *, check_title: bool = False) -> None:
        with self.scope.lock:
            self.scope.refresh()
            current = self.scope.grants().get(grant.contact.id)
            if self.stop.is_set() or current is None or current.revision != grant.revision:
                raise RpaNotReady("会话接待权限已撤销或暂停")
            if check_title and (
                current.contact.title != grant.contact.title or not self.scope.unique_title(grant.contact.id)
            ):
                raise RpaNotReady("会话已改名或名称不唯一，需要人工核对")

    def _active(self, grant: Grant) -> bool:
        try:
            self.authorize(grant)
        except RpaNotReady:
            return False
        return self.healthy

    def _publish(self, grants: dict[str, Grant], *, force: bool = False) -> None:
        targets = [
            {"identity": cloud_identity(self.client.account_tag, grant), "keywords": list(grant.keywords)}
            for _, grant in sorted(grants.items())
        ]
        now = time.monotonic()
        if force or targets != self.published or now >= self.next_publish:
            self.client.publish_scope(targets)
            self.published, self.next_publish = targets, now + 12

    def _worker(self, grant: Grant) -> Worker:
        contact = grant.contact
        path = self.target_runtime(grant)
        store = TgoStore(path / "state.sqlite3", chat_id=contact.id)
        client = TgoClient(
            self.client.config_file, path, self.client.account_tag, scoped=True,
            identity=cloud_identity(self.client.account_tag, grant),
            nickname=(contact.remark or contact.nickname or ("微信群聊" if contact.is_group else "微信联系人"))[:80],
        )
        reader = WeChatReader(self.key_file, contact.id, scoped=True)
        return Worker(grant, TgoBridge(
            reader, store, path, "", client, None, self.allow_send, float("inf"), float("inf"),
            scoped=True, active=lambda: self._active(grant),
        ))

    def _reconcile(self, grants: dict[str, Grant]) -> None:
        for identity, worker in list(self.workers.items()):
            current = grants.get(identity)
            if current is None or current.revision != worker.grant.revision:
                worker.bridge.stop.set()
                worker.bridge.ledger.close()
                del self.workers[identity]
        for identity, grant in grants.items():
            if identity not in self.workers:
                self.workers[identity] = self._worker(grant)

    def _ingest(self) -> None:
        if not self.workers:
            return
        reader = self.scope.reader
        known = {entry.path for entry in reader.shards}
        found = {path.resolve() for path in reader.root.rglob("message_*.db")
                 if path.stem.removeprefix("message_").isdigit()}
        if found - known:
            raise ValueError("发现新消息分库，请重新执行本机取钥")
        pending = {key: {} for key, item in self.workers.items() if not item.bridge.ledger.initialized()}
        for entry in reader.shards:
            shard = reader.shard_name(entry.path)
            signature = reader._signature(entry.path)
            if not pending and self.signatures.get(shard) == signature:
                continue
            full = False
            connection = open_snapshot(entry.path, entry.key)
            try:
                tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for identity, worker in self.workers.items():
                    if not self._active(worker.grant):
                        continue
                    table = conversation_table(identity)
                    exists = table in tables
                    if identity in pending:
                        pending[identity][shard] = int(connection.execute(
                            f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"'
                        ).fetchone()[0]) if exists else 0
                        continue
                    if not exists:
                        continue
                    cursor = worker.bridge.ledger.cursors().get(shard, 0)
                    maximum = connection.execute(f'SELECT COALESCE(MAX(local_id),0) FROM "{table}"').fetchone()[0]
                    if maximum < cursor:
                        worker.error = "消息序号回退，暂停该会话"
                        continue
                    if not worker.error and maximum > cursor:
                        batch = read_messages(connection, shard, identity, reader.self_username, cursor, 100)
                        worker.bridge.observe(batch)
                        full |= len(batch) == 100
            finally:
                connection.close()
            if not full:
                self.signatures[shard] = signature
        for identity, maxima in pending.items():
            worker = self.workers[identity]
            if self._active(worker.grant):
                worker.bridge.ledger.baseline(maxima)

    def _load_backend(self, worker: Worker) -> None:
        from .scoped_rpa import ScopedRpa

        current = self.scope.grants().get(worker.grant.contact.id)
        if self.window is None or not self.window_ready or current is None:
            worker.bridge.backend = None
            return
        if worker.bridge.backend is not None and current.contact.title == worker.target_title:
            return

        def check() -> None:
            if not self.window_ready or not self.healthy:
                raise RpaNotReady("微信窗口暂不可用，回复待人工处理")
            self.authorize(current, check_title=True)

        worker.bridge.backend = ScopedRpa(
            self.window, worker.bridge.runtime, current.contact.id, current.contact.title,
            check, self.scope.lock, worker.bridge.reader.draft_matches,
        )
        worker.target_title = current.contact.title

    def _process(self, worker: Worker) -> None:
        bridge = worker.bridge
        if worker.error or not bridge.ledger.initialized() or not self._active(worker.grant):
            return
        if not worker.connected and not bridge.ledger.next_inbound() and bridge.ledger.value("tgo_channel") is None:
            return
        try:
            if not worker.connected:
                bridge.initialize()
                worker.connected = True
            elif visitor_token_expired(bridge.client.session.get("im_token")):
                bridge.client.connect()
            self._load_backend(worker)
            bridge.poll(read_local=False)
        except TgoUnavailable:
            worker.next_poll = time.monotonic() + 6
        except (RuntimeError, ValueError, OSError, sqlite3.Error, KeyError) as exc:
            reason = str(exc) if isinstance(exc, (TgoError, RpaNotReady)) else type(exc).__name__
            worker.error = f"需人工核对（{reason}）"

    def poll(self) -> None:
        self.scope.refresh()
        grants = self.scope.grants()
        self._publish(grants)
        self.healthy = self.scope.ready
        self._reconcile(grants)
        self._ingest()
        # Serialize desktop access. A window check only holds the send
        # path; reading and configuring scope can continue.
        if not self.desktop_lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            due = sorted(self.workers.values(), key=lambda worker: worker.next_poll)
            for worker in due[:8]:
                if self.stop.is_set():
                    break
                if worker.next_poll <= now:
                    worker.next_poll = time.monotonic() + 2
                    self._process(worker)
        finally:
            self.desktop_lock.release()

    def run(self) -> None:
        try:
            while not self.stop.is_set():
                try:
                    with self.lock:
                        self.poll()
                        self.error = self.scope.error
                except (TgoError, ValueError, OSError, sqlite3.Error) as exc:
                    self.healthy = False
                    reason = str(exc) if isinstance(exc, (TgoError, TgoUnavailable)) else type(exc).__name__
                    self.error = f"接待暂停（{reason}）"
                    try:
                        self._publish({}, force=True)
                    except (TgoError, OSError):
                        pass  # The cloud grant will expire on its own.
                active_ids = self.scope.grants()
                self.status_snapshot = {
                    key: {
                        "state": worker.error or worker.bridge.rpa_status,
                        "sent": worker.bridge.ledger.delivery_stats().get("sent", 0),
                    }
                    for key, worker in self.workers.items() if key in active_ids
                }
                self.stop.wait(1)
        finally:
            self.healthy = False
            try:
                self._publish({}, force=True)
            except (TgoError, OSError):
                pass
            for worker in self.workers.values():
                worker.bridge.ledger.close()

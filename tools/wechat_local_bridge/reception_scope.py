"""Local-only catalogue and explicit authorization for customer conversations."""

from __future__ import annotations

import json
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from .crypto import open_snapshot
from .reader import WeChatReader
from .window_ocr import normalized_title


def is_customer_chat(value: str) -> bool:
    return bool(re.fullmatch(r"[\w.-]+(?:@chatroom|@openim)?", value, re.ASCII)) and (
        len(value) <= 128
        and not value.startswith("gh_")
        and value not in {"filehelper", "weixin", "fmessage", "newsapp"}
    )


@dataclass(frozen=True)
class Contact:
    id: str
    remark: str
    nickname: str
    is_group: bool
    selectable: bool

    @property
    def title(self) -> str:
        return self.remark or self.nickname or self.id


def load_contacts(reader: WeChatReader) -> dict[str, Contact]:
    entries = [entry for entry in reader.databases if entry.path.name == "contact.db"]
    if len(entries) != 1:
        raise ValueError("需要重新取钥以打开当前微信账号的联系人库")
    entry = entries[0]
    connection = open_snapshot(entry.path, entry.key)
    try:
        rows = connection.execute("""
            SELECT c.username, c.remark, c.nick_name, c.local_type, c.flag,
                   c.delete_flag, c.verify_flag, c.is_in_chat_room,
                   EXISTS(SELECT 1 FROM chat_room r WHERE r.username=c.username)
            FROM contact c
        """).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ValueError("联系人库未返回可验证的完整列表")
    result = {}
    for identity, remark, name, kind, flag, deleted, verified, member, room in rows:
        if not isinstance(identity, str) or not is_customer_chat(identity) or identity == reader.self_username:
            continue
        group = identity.endswith("@chatroom")
        friend_type = 5 if identity.endswith("@openim") else 1
        selectable = deleted == 0 and (
            member == 1 and bool(room)
            if group
            else kind == friend_type and isinstance(flag, int) and bool(flag & 1) and verified == 0
        )
        if identity in result:
            raise ValueError("联系人库包含重复内部标识")
        result[identity] = Contact(identity, str(remark or "").strip(), str(name or "").strip(), group, selectable)
    return result


@dataclass(frozen=True)
class ContactGroup:
    name: str
    members: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class ScopeSettings:
    enabled: bool = False
    individuals: tuple[str, ...] = ()
    paused: tuple[str, ...] = ()
    groups: tuple[ContactGroup, ...] = ()
    rooms: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: object) -> "ScopeSettings":
        if not isinstance(raw, dict) or not isinstance(raw.get("enabled"), bool):
            raise ValueError("接待范围格式无效")

        def people(values: object) -> tuple[str, ...]:
            if not isinstance(values, list) or len(values) > 1000 or any(
                not isinstance(x, str) or not is_customer_chat(x) or x.endswith("@chatroom") for x in values
            ):
                raise ValueError("联系人列表无效")
            return tuple(dict.fromkeys(values))

        groups = raw.get("groups", [])
        if not isinstance(groups, list) or len(groups) > 100:
            raise ValueError("分组列表无效")
        parsed = []
        names = set()
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("enabled"), bool):
                raise ValueError("分组格式无效")
            name = group.get("name", "")
            if not isinstance(name, str) or not 0 < len(name.strip()) <= 60 or name.strip() in names:
                raise ValueError("分组名称不能为空或重复")
            names.add(name.strip())
            parsed.append(ContactGroup(name.strip(), people(group.get("members", [])), group["enabled"]))
        rooms = raw.get("rooms", {})
        if not isinstance(rooms, dict) or len(rooms) > 1000:
            raise ValueError("群聊列表无效")
        parsed_rooms = {}
        for identity, keywords in rooms.items():
            if (
                not isinstance(identity, str) or not is_customer_chat(identity) or not identity.endswith("@chatroom")
                or not isinstance(keywords, list) or len(keywords) > 30
                or any(not isinstance(word, str) or not 0 < len(word.strip()) <= 80 for word in keywords)
            ):
                raise ValueError("群聊或关键词格式无效")
            parsed_rooms[identity] = tuple(dict.fromkeys(word.strip() for word in keywords))
        settings = cls(raw["enabled"], people(raw.get("individuals", [])), people(raw.get("paused", [])),
                       tuple(parsed), parsed_rooms)
        if len(settings.selected()) > 1000:
            raise ValueError("接待范围最多包含 1000 个会话")
        return settings

    def selected(self) -> set[str]:
        people = set(self.individuals)
        for group in self.groups:
            if group.enabled:
                people.update(group.members)
        return (people - set(self.paused)) | set(self.rooms)


@dataclass(frozen=True)
class Grant:
    contact: Contact
    revision: str
    keywords: tuple[str, ...]


class Scope:
    def __init__(self, reader: WeChatReader, path: Path):
        self.reader, self.path = reader, path
        self.lock = threading.RLock()
        self.contacts: dict[str, Contact] = {}
        self.signature = None
        self.error = ""
        self.ready = False
        self.account = str(reader.root)
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if saved and saved.get("account") != self.account:
            raise ValueError("接待范围属于其他微信账号")
        self.settings = ScopeSettings.parse(saved["settings"]) if saved else ScopeSettings()
        self.suspended: set[str] = set(saved.get("suspended", []))
        self.revisions: dict[str, str] = saved.get("revisions", {})

    def _write(self) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({
            "account": self.account, "settings": asdict(self.settings),
            "suspended": sorted(self.suspended), "revisions": self.revisions,
        }, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)

    def refresh(self, force: bool = False) -> None:
        with self.lock:
            try:
                paths = [entry.path for entry in self.reader.databases if entry.path.name == "contact.db"]
                signature = tuple(self.reader._signature(path) for path in paths)
                if not force and self.ready and self.signature == signature:
                    return
                self.contacts = load_contacts(self.reader)
            except (OSError, ValueError, StopIteration) as exc:
                self._failed(type(exc).__name__)
                return
            except Exception as exc:
                # Invalid encrypted pages, partial copies and unknown schemas all pause access.
                self._failed(type(exc).__name__)
                return
            self.ready, self.signature, self.error = True, signature, ""
            self._reconcile()

    def _failed(self, reason: str) -> None:
        self.ready, self.signature = False, None
        self.error = f"联系人暂不可用，接待已暂停（{reason}）"
        if self.revisions:
            self.revisions = {}
            self._write()

    def _reconcile(self) -> None:
        previous = (set(self.suspended), self.revisions.copy())
        selected = self.settings.selected()
        available = {key for key, contact in self.contacts.items() if contact.selectable}
        self.suspended.update(selected - available)
        active = (selected & available) - self.suspended if self.settings.enabled else set()
        self.revisions = {key: self.revisions.get(key) or uuid4().hex for key in active}
        if previous != (self.suspended, self.revisions):
            self._write()

    def save(self, settings: ScopeSettings, resume: list[str]) -> None:
        with self.lock:
            self.refresh()
            if not self.ready:
                if not settings.enabled and self.settings.enabled and not resume:
                    self.settings = ScopeSettings(False, self.settings.individuals, self.settings.paused,
                                                  self.settings.groups, self.settings.rooms)
                    self.revisions = {}
                    self._write()
                    return
                raise ValueError("请先刷新联系人再修改接待范围")
            old_selection = self.settings.selected()
            for key in settings.selected() - old_selection:
                if key not in self.contacts or not self.contacts[key].selectable:
                    raise ValueError("选择的联系人或群聊已不可用")
            if any(key not in self.suspended or key not in self.contacts or not self.contacts[key].selectable for key in resume):
                raise ValueError("只可恢复已重新出现的联系人或群聊")
            self.suspended.difference_update(resume)
            self.settings = settings
            self._reconcile()
            self._write()

    def grants(self) -> dict[str, Grant]:
        with self.lock:
            if not self.ready:
                return {}
            return {
                key: Grant(self.contacts[key], revision, self.settings.rooms.get(key, ()))
                for key, revision in self.revisions.items()
            }

    def unique_title(self, identity: str) -> bool:
        contact = self.contacts[identity]
        title = normalized_title(contact.title)
        aliases = Counter(
            alias for other in self.contacts.values()
            for alias in {normalized_title(value) for value in (other.title, other.nickname, other.remark)} if alias
        )
        return bool(contact.remark or contact.nickname) and aliases[title] == 1 and not any(
            title != alias and (title in alias or alias in title)
            for alias in aliases if len(alias) >= 2
        )

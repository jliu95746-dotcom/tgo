"""Resolve media by exact conversation and message IDs, never by newest file."""

from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re

from .crypto import open_snapshot
from .image_keys import ImageKeys
from .image_media import V2, decode_image
from .media_types import MAX_MEDIA_BYTES, MediaContent, MediaNotReady
from .reader import Message, WeChatReader, read_messages


class LocalMediaReader:
    def __init__(
        self,
        reader: WeChatReader,
        key_file: Path,
        runtime: Path,
        active: Callable[[], bool],
    ):
        self.reader, self.active = reader, active
        self.keys = ImageKeys(key_file, key_file.parent, reader.root.parent, active)

    def _allowed(self) -> None:
        if not self.active():
            raise MediaNotReady("会话已暂停接待，停止读取媒体")

    def _source(self, message: Message) -> Message:
        self._allowed()
        if message.chat_id != self.reader.chat_id or message.message_type not in (
            3,
            34,
        ):
            raise MediaNotReady("媒体消息不属于当前授权会话")
        entry = next(
            (
                entry
                for entry in self.reader.shards
                if self.reader.shard_name(entry.path) == message.shard
            ),
            None,
        )
        if entry is None:
            raise MediaNotReady("原始媒体消息库暂不可用")
        with closing(open_snapshot(entry.path, entry.key)) as db:
            rows = read_messages(
                db,
                message.shard,
                message.chat_id,
                self.reader.self_username,
                message.local_id - 1,
                1,
            )
        if not rows:
            raise MediaNotReady("原始媒体消息暂不可用")
        actual = rows[0]
        if (
            actual.local_id,
            actual.created_at,
            actual.message_type,
            actual.sender_id,
        ) != (
            message.local_id,
            message.created_at,
            message.message_type,
            message.sender_id,
        ) or message.server_id not in (
            "",
            "0",
            actual.server_id,
        ):
            raise MediaNotReady("媒体消息身份未通过核对")
        return actual

    def extract(self, message: Message) -> MediaContent:
        source = self._source(message)
        result = (
            self._image(source) if source.message_type == 3 else self._voice(source)
        )
        self._allowed()
        return result

    def _image(self, message: Message) -> MediaContent:
        entry = next(
            (
                entry
                for entry in self.reader.databases
                if entry.path.name == "message_resource.db"
            ),
            None,
        )
        if entry is None:
            raise MediaNotReady("图片资源索引尚未就绪")
        with closing(open_snapshot(entry.path, entry.key)) as db:
            rows = db.execute(
                "SELECT m.packed_info FROM MessageResourceInfo m JOIN ChatName2Id c ON c.rowid=m.chat_id "
                "WHERE c.user_name=? AND m.message_local_id=? AND m.message_svr_id=? "
                "AND m.message_create_time=? AND m.message_local_type=3",
                (
                    message.chat_id,
                    message.local_id,
                    message.server_id,
                    message.created_at,
                ),
            ).fetchall()
        if (
            len(rows) != 1
            or not isinstance(rows[0][0], bytes)
            or len(rows[0][0]) > 65536
        ):
            raise MediaNotReady("微信图片资源尚未唯一匹配")
        names = set(re.findall(rb"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", rows[0][0]))
        if len(names) != 1:
            raise MediaNotReady("微信图片文件名尚未核验")
        name = next(iter(names)).decode("ascii")
        account = self.reader.root.parent.resolve()
        base = (
            account
            / "msg"
            / "attach"
            / hashlib.md5(message.chat_id.encode()).hexdigest()
        )
        date = datetime.fromtimestamp(message.created_at)
        months = {
            (date + timedelta(days=offset)).strftime("%Y-%m") for offset in (-1, 0, 1)
        }
        months.add(
            datetime.fromtimestamp(message.created_at, timezone.utc).strftime("%Y-%m")
        )
        candidates: list[tuple[Path, bytes]] = []
        for month in sorted(months):
            for suffix in ("_h", "", "_t"):
                path = base / month / "Img" / (name + suffix + ".dat")
                self._allowed()
                if not path.is_file():
                    continue
                if (
                    not path.resolve().is_relative_to(account)
                    or not 0 < path.stat().st_size <= MAX_MEDIA_BYTES + 128
                ):
                    raise MediaNotReady("图片路径或大小未通过核对")
                candidates.append((path, path.read_bytes()))
        if not candidates:
            raise MediaNotReady("微信图片尚未下载到本机，稍后重试")
        key = None
        encrypted = [(path, data) for path, data in candidates if data.startswith(V2)]
        if encrypted:
            sample = min(
                encrypted,
                key=lambda item: (not item[0].stem.endswith("_t"), len(item[1])),
            )
            key = self.keys.get(sample[1])
        for _path, data in sorted(
            candidates, key=lambda item: len(item[1]), reverse=True
        ):
            self._allowed()
            try:
                return decode_image(data, key, self.keys.xor_key)
            except ValueError:
                continue
        raise MediaNotReady("图片尚不能完整解码，等待微信图片就绪")

    def _voice(self, message: Message) -> MediaContent:
        payloads: set[bytes] = set()
        for entry in self.reader.databases:
            if not re.fullmatch(r"media_\d+\.db", entry.path.name):
                continue
            self._allowed()
            with closing(open_snapshot(entry.path, entry.key)) as db:
                rows = db.execute(
                    "SELECT v.voice_data FROM VoiceInfo v JOIN Name2Id n ON n.rowid=v.chat_name_id "
                    "WHERE n.user_name=? AND v.local_id=? AND v.svr_id=? AND v.create_time=?",
                    (
                        message.chat_id,
                        message.local_id,
                        message.server_id,
                        message.created_at,
                    ),
                ).fetchall()
                for row in rows:
                    data = row[0]
                    if isinstance(data, bytes) and 0 < len(data) <= 2 * 1024**2:
                        payloads.add(data)
        if len(payloads) != 1:
            raise MediaNotReady("微信语音尚未唯一匹配，等待下载后重试")
        data = next(iter(payloads))
        if not data.startswith((b"\x02#!SILK_V3", b"#!SILK_V3")):
            raise MediaNotReady("该语音编码暂不支持")
        return MediaContent("voice", "audio/silk", data)

"""Remember only this bridge's temporary draft so interrupted checks can resume."""

from collections.abc import Callable
import json
import os
from pathlib import Path
import re

from .rpa import RpaNotReady


class DraftProbe:
    def __init__(self, runtime: Path, chat_id: str):
        self.path = runtime / "rpa-probe.private.json"
        self.chat_id = chat_id

    def pending(self) -> str | None:
        try:
            if not self.path.exists():
                return None
            if self.path.stat().st_size > 1024:
                raise ValueError("oversized probe")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            marker = data["marker"]
            if data["chat_id"] != self.chat_id or not isinstance(marker, str) or not re.fullmatch(r"桥接自检-[0-9a-f]{32}", marker):
                raise ValueError("different probe")
            return marker
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise RpaNotReady("本机校验记录无法核对，保留草稿并暂停填写") from error

    def remember(self, marker: str) -> None:
        if not re.fullmatch(r"桥接自检-[0-9a-f]{32}", marker):
            raise ValueError("仅允许保存本工具生成的校验标记")
        if self.pending() not in (None, marker):
            raise RpaNotReady("上一次本机校验尚未恢复，保留草稿")
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump({"chat_id": self.chat_id, "marker": marker}, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def complete(self, marker: str) -> None:
        pending = self.pending()
        if pending is None:
            return
        if pending != marker:
            raise RpaNotReady("本机校验记录已变化，保留草稿")
        self.path.unlink()

    def recover(
        self, read_editor: Callable[[], str], matches_target: Callable[[str], bool],
        clear_marker: Callable[[str], None], *, keep_marker: bool = False,
    ) -> bool:
        marker = self.pending()
        if marker is None:
            return False
        current = read_editor()
        if current == marker and matches_target(marker):
            if not keep_marker:
                clear_marker(marker)
                self.complete(marker)
            return True
        elif current or matches_target(marker):
            raise RpaNotReady("本机校验草稿尚未核对，保留原内容并稍后重试")
        self.complete(marker)
        return False

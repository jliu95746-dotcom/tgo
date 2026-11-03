"""Protect only this tool's dedicated runtime directory, never the WeChat source."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path
from uuid import UUID


DEFAULT_RUNTIME = Path(__file__).resolve().parents[2] / ".tmp/wechat-local-bridge"


def _scoped_data_root() -> Path | None:
    value = os.environ.get("LOCALAPPDATA")
    if os.name == "nt" and value and Path(value).is_absolute():
        return Path(value) / "Yujian" / "WeChatBridge"
    return None


def scoped_runtime(platform_id: UUID) -> Path:
    legacy = DEFAULT_RUNTIME / "scoped-channels" / str(platform_id)
    root = _scoped_data_root()
    if root is None:
        return legacy
    try:
        if (legacy / "tgo.private.json").is_file():
            return legacy
    except OSError as error:
        if getattr(error, "winerror", None) != 206:
            raise
    return root / str(platform_id)


def ensure_private_runtime(path: Path, *, scoped: bool = False) -> None:
    roots = [DEFAULT_RUNTIME.resolve()]
    if scoped and (root := _scoped_data_root()):
        roots.append(root.resolve())
    target = path.resolve()
    if not any(target.is_relative_to(allowed) for allowed in roots):
        raise ValueError("运行目录必须位于本工具专用目录内，避免修改其他目录权限")
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        sid = next(csv.reader(identity.stdout.strip().splitlines()))[1]
        result = subprocess.run(
            [
                "icacls.exe",
                str(target),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:(OI)(CI)F",
                "*S-1-5-18:(OI)(CI)F",
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError("无法保护私有运行目录，停止读写敏感数据")
    else:
        target.chmod(0o700)

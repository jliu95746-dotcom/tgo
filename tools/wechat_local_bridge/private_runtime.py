"""Protect only this tool's dedicated runtime directory, never the WeChat source."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path


def ensure_private_runtime(path: Path) -> None:
    allowed = Path(__file__).resolve().parents[2] / ".tmp/wechat-local-bridge"
    target = path.resolve()
    if target != allowed and not target.is_relative_to(allowed):
        raise ValueError(
            "运行目录必须位于本工具专用 .tmp/wechat-local-bridge 内，避免修改其他目录权限"
        )
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

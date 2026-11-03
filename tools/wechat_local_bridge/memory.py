"""Read-only Windows key capture; the only process access requested is 0x0410."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import time
from pathlib import Path
from typing import TypedDict

from . import _scanner_vendor as scanner


class CapturedDatabase(TypedDict):
    path: str
    relative_path: str
    key: str


class MemoryRegion(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64),
        ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


class ReadOnlyMemory:
    def __init__(self, pid: int, timeout: int = 120):
        if os.name != "nt" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("此取钥实现需要 Windows 64 位 Python")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.ReadProcessMemory.argtypes = [
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self.kernel.ReadProcessMemory.restype = wt.BOOL
        self.kernel.VirtualQueryEx.argtypes = [
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.POINTER(MemoryRegion),
            ctypes.c_size_t,
        ]
        self.kernel.VirtualQueryEx.restype = ctypes.c_size_t
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wt.HANDLE,
            wt.DWORD,
            wt.LPWSTR,
            ctypes.POINTER(wt.DWORD),
        ]
        self.handle = self.kernel.OpenProcess(0x0410, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        name = ctypes.create_unicode_buffer(32768)
        size = wt.DWORD(len(name))
        if (
            not self.kernel.QueryFullProcessImageNameW(
                self.handle, 0, name, ctypes.byref(size)
            )
            or Path(name.value).name.lower() != "weixin.exe"
        ):
            self.close()
            raise ValueError("PID 不属于已选择的 Weixin.exe，拒绝扫描")
        self.deadline = time.monotonic() + timeout
        self.read_bytes = 0

    def regions(self) -> list[tuple[int, int]]:
        result = []
        address = 0
        region = MemoryRegion()
        while self.kernel.VirtualQueryEx(
            self.handle,
            ctypes.c_void_p(address),
            ctypes.byref(region),
            ctypes.sizeof(region),
        ):
            if (
                region.State == 0x1000
                and not region.Protect & (0x100 | 0x01)
                and region.RegionSize
            ):
                result.append((region.BaseAddress, region.RegionSize))
            after = region.BaseAddress + region.RegionSize
            if after <= address:
                break
            address = after
        return result

    def read(self, address: int, size: int) -> bytes:
        if time.monotonic() > self.deadline or self.read_bytes > 16 * 1024**3:
            raise TimeoutError("只读扫描达到本轮时间/字节上限")
        if not 0 < size <= 2 * 1024**2:
            raise ValueError("内存读取范围无效")
        output = ctypes.create_string_buffer(size)
        count = ctypes.c_size_t()
        okay = self.kernel.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), output, size, ctypes.byref(count)
        )
        self.read_bytes += count.value
        return output.raw[: count.value] if okay or count.value else b""

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def save_keys(runtime: Path, pid: int, databases: list[CapturedDatabase]) -> None:
    if not databases:
        raise ValueError("未验证任何密钥，保留既有密钥文件")
    runtime.mkdir(parents=True, exist_ok=True)
    target = runtime / "keys.private.json"
    temp = runtime / "keys.private.tmp"
    temp.write_text(json.dumps({"pid": pid, "databases": databases}), encoding="utf-8")
    temp.replace(target)


def capture(pid: int, account_root: Path, runtime: Path) -> dict[str, int]:
    root = (account_root / "db_storage").resolve()
    if not root.is_dir() or runtime.resolve().is_relative_to(account_root.resolve()):
        raise ValueError("账号目录无效，或运行目录位于微信源目录内")
    files = []
    salts: dict[str, list[str]] = {}
    for candidate in root.rglob("*.db"):
        path = candidate.resolve()
        if not path.is_relative_to(root):
            continue
        with path.open("rb") as stream:
            page = stream.read(4096)
        if len(page) != 4096 or page.startswith(b"SQLite format 3"):
            continue
        salt = page[:16].hex()
        name = path.relative_to(root).as_posix()
        files.append((name, str(path), path.stat().st_size, salt, page))
        salts.setdefault(salt, []).append(name)
    if not files:
        raise ValueError("账号目录内未找到可验证的加密数据库")
    memory = ReadOnlyMemory(pid)
    try:
        keys: dict[str, str] = {}
        stats = scanner._scan_windows_v411_config_cipher(
            pid,
            memory.regions(),
            memory.read,
            memory.read,
            files,
            salts,
            keys,
            set(salts),
            lambda _message: None,
        )
        captured: list[CapturedDatabase] = [
            {"path": path, "relative_path": name, "key": keys[salt]}
            for name, path, _size, salt, _page in files
            if salt in keys
        ]
        save_keys(runtime, pid, captured)
        report = {
            "pid": pid,
            "database_count": len(files),
            "verified_databases": len(captured),
            "candidate_count": stats["candidate_count"],
            "read_bytes": memory.read_bytes,
        }
        (runtime / "capture-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report
    finally:
        memory.close()

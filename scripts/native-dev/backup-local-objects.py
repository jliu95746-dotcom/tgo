"""Back up the three local Compose upload/media volumes independently of PostgreSQL."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
from typing import Sequence
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = REPO_ROOT / ".tmp" / "backups"
RESTORE_ROOT = REPO_ROOT / ".tmp" / "restore-rehearsal"
OBJECT_ROOTS = (
    "data/tgo-api/uploads",
    "data/tgo-rag/uploads",
    "data/tgo-platform/media",
)
FORMAT = "tgo-local-objects-v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackupError(RuntimeError):
    """An operator-safe error that does not include file contents."""


@dataclass(frozen=True)
class VerifiedObjectBackup:
    manifest: Path
    files: tuple[tuple[PurePosixPath, int, str], ...]
    file_count: int
    total_bytes: int


def _storage_mode(env_path: Path) -> str:
    mode = os.environ.get("STORAGE_TYPE", "").strip()
    if not mode:
        if not env_path.is_file():
            raise BackupError("缺少 .env.dev，无法确认当前存储模式")
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            name, separator, value = line.partition("=")
            if separator and name.strip() == "STORAGE_TYPE":
                mode = value.strip().strip("\"'")
    mode = mode.lower() or "local"
    if mode != "local":
        raise BackupError(f"当前存储模式为 {mode}，本地目录备份不能代替该对象存储的备份")
    return mode


def _safe_remove(directory: Path, parent: Path) -> None:
    directory = directory.resolve(strict=True)
    parent = parent.resolve(strict=True)
    if directory.parent != parent or not directory.name.startswith("objects-"):
        raise BackupError("拒绝清理非本次生成的目录")
    shutil.rmtree(directory)


def _list_source_files(repo_root: Path) -> list[tuple[PurePosixPath, Path]]:
    records: list[tuple[PurePosixPath, Path]] = []
    for relative_root in OBJECT_ROOTS:
        source_root = repo_root / relative_root
        if not source_root.is_dir() or source_root.is_symlink():
            raise BackupError(f"本地对象目录不可用：{relative_root}")
        for current, directories, files in os.walk(source_root, followlinks=False):
            for name in (*directories, *files):
                path = Path(current) / name
                if path.is_symlink():
                    raise BackupError("对象目录中存在符号链接，备份已停止")
            for name in files:
                source = Path(current) / name
                if not stat.S_ISREG(source.stat(follow_symlinks=False).st_mode):
                    raise BackupError("对象目录中存在非普通文件，备份已停止")
                relative = PurePosixPath(*source.relative_to(repo_root).parts)
                records.append((relative, source))
    return sorted(records, key=lambda item: str(item[0]))


def _copy_with_hash(source: Path, destination: Path) -> tuple[int, str]:
    before = source.stat(follow_symlinks=False)
    digest = hashlib.sha256()
    size = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        while block := reader.read(1024 * 1024):
            writer.write(block)
            digest.update(block)
            size += len(block)
    after = source.stat(follow_symlinks=False)
    if (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ) or size != after.st_size:
        raise BackupError("备份期间源文件发生变化，请重试")
    return size, digest.hexdigest()


def create_backup(repo_root: Path = REPO_ROOT, backup_root: Path = BACKUP_ROOT) -> Path:
    repo_root = repo_root.resolve(strict=True)
    expected_root = (repo_root / ".tmp" / "backups").resolve(strict=False)
    requested_root = backup_root.resolve(strict=False)
    if requested_root != expected_root or not requested_root.is_relative_to(repo_root):
        raise BackupError("本地对象备份目标目录不在项目内")
    _storage_mode(repo_root / ".env.dev")
    source_files = _list_source_files(repo_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_root = backup_root.resolve(strict=True)
    directory = backup_root / (
        "objects-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex
    )
    directory.mkdir(mode=0o700)
    try:
        entries: list[dict[str, object]] = []
        for relative, source in source_files:
            size, digest = _copy_with_hash(
                source, directory / "objects" / Path(*relative.parts)
            )
            entries.append({"path": str(relative), "bytes": size, "sha256": digest})
        manifest = directory / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "format": FORMAT,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "source_roots": OBJECT_ROOTS,
                    "files": entries,
                    "file_count": len(entries),
                    "total_bytes": sum(entry["bytes"] for entry in entries),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        verify_backup(manifest, backup_root)
        return manifest
    except (OSError, BackupError, ValueError, TypeError) as exc:
        try:
            _safe_remove(directory, backup_root)
        except (OSError, BackupError) as cleanup_error:
            raise BackupError(f"备份失败且临时目录清理失败：{directory.name}") from cleanup_error
        if isinstance(exc, BackupError):
            raise
        raise BackupError("本地对象备份失败") from exc


def _validated_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or "\\" in value:
        raise BackupError("备份清单包含无效路径")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise BackupError("备份清单包含越界路径")
    if not any(path.is_relative_to(PurePosixPath(root)) for root in OBJECT_ROOTS):
        raise BackupError("备份清单包含非对象目录路径")
    return path


def verify_backup(
    manifest_path: Path, backup_root: Path = BACKUP_ROOT
) -> VerifiedObjectBackup:
    try:
        root = backup_root.resolve(strict=True)
        manifest = manifest_path.resolve(strict=True)
        if not manifest.is_relative_to(root) or manifest.name != "manifest.json":
            raise BackupError("备份清单路径不在预期目录")
        if manifest.stat().st_size > 4 * 1024 * 1024:
            raise BackupError("备份清单过大")
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
        if (
            payload.get("format") != FORMAT
            or tuple(payload.get("source_roots", ())) != OBJECT_ROOTS
            or type(payload.get("file_count")) is not int
            or type(payload.get("total_bytes")) is not int
            or not isinstance(payload.get("files"), list)
        ):
            raise BackupError("备份清单格式无效")
        objects_root = manifest.parent / "objects"
        if not objects_root.is_dir() or objects_root.is_symlink():
            raise BackupError("备份对象目录不可用")
        entries: list[tuple[PurePosixPath, int, str]] = []
        expected: set[PurePosixPath] = set()
        for entry in payload["files"]:
            relative = _validated_relative(entry["path"])
            size, digest = entry["bytes"], entry["sha256"]
            if (
                relative in expected
                or type(size) is not int
                or size < 0
                or not isinstance(digest, str)
                or not SHA256.fullmatch(digest)
            ):
                raise BackupError("备份清单文件记录无效")
            expected.add(relative)
            entries.append((relative, size, digest))
        if (
            len(entries) != payload["file_count"]
            or sum(item[1] for item in entries) != payload["total_bytes"]
        ):
            raise BackupError("备份清单数量或大小不一致")
        actual: set[PurePosixPath] = set()
        for current, directories, files in os.walk(objects_root, followlinks=False):
            for name in (*directories, *files):
                if (Path(current) / name).is_symlink():
                    raise BackupError("备份对象目录包含符号链接")
            for name in files:
                file_path = Path(current) / name
                if not file_path.is_file():
                    raise BackupError("备份对象目录包含非普通文件")
                actual.add(PurePosixPath(*file_path.relative_to(objects_root).parts))
        if actual != expected:
            raise BackupError("备份清单与对象文件集合不一致")
        for relative, size, digest in entries:
            file_path = objects_root / Path(*relative.parts)
            if file_path.stat().st_size != size:
                raise BackupError("备份对象文件大小不一致")
            file_hash = hashlib.sha256()
            with file_path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    file_hash.update(block)
            if file_hash.hexdigest() != digest:
                raise BackupError("备份对象文件 SHA256 不一致")
        return VerifiedObjectBackup(
            manifest, tuple(entries), len(entries), payload["total_bytes"]
        )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise BackupError("备份清单或对象文件无效") from exc


def rehearse_restore(
    backup: VerifiedObjectBackup, restore_root: Path = RESTORE_ROOT
) -> dict[str, object]:
    restore_root.mkdir(parents=True, exist_ok=True)
    restore_root = restore_root.resolve(strict=True)
    directory = restore_root / ("objects-" + uuid4().hex)
    directory.mkdir(mode=0o700)
    failure: BackupError | None = None
    try:
        for relative, size, digest in backup.files:
            source = backup.manifest.parent / "objects" / Path(*relative.parts)
            destination = directory / Path(*relative.parts)
            copied_size, copied_hash = _copy_with_hash(source, destination)
            if copied_size != size or copied_hash != digest:
                raise BackupError("隔离恢复文件校验失败")
    except (OSError, BackupError) as exc:
        failure = exc if isinstance(exc, BackupError) else BackupError("隔离恢复复制失败")
    try:
        _safe_remove(directory, restore_root)
    except (OSError, BackupError) as exc:
        raise BackupError(f"隔离恢复临时目录清理失败：{directory.name}") from exc
    if failure is not None:
        raise failure
    return {
        "restore_performed": True,
        "restored_files": backup.file_count,
        "restored_bytes": backup.total_bytes,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份默认本地 Compose 的上传与媒体目录")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--backup", action="store_true", help="创建独立本地对象备份")
    source.add_argument("--manifest", type=Path, help="校验已有备份清单")
    parser.add_argument("--rehearse", action="store_true", help="复制到隔离目录并校验，然后清理")
    args = parser.parse_args(argv)
    try:
        manifest = create_backup() if args.backup else args.manifest
        verified = verify_backup(manifest)
        report: dict[str, object] = {
            "backup_verified": True,
            "file_count": verified.file_count,
            "total_bytes": verified.total_bytes,
            "manifest": str(verified.manifest),
            "restore_performed": False,
        }
        if args.rehearse:
            report.update(rehearse_restore(verified))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except BackupError as exc:
        print(f"本地对象备份失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

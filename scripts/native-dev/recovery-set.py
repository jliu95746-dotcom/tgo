"""Verify and rehearse the database and local-object backups as one set.

Both archives are checked before any restore. The rehearsal uses the existing
isolated database and disposable object directory; it never restores in place.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Sequence


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
BACKUP_ROOT = SCRIPT_DIRECTORY.parents[1] / ".tmp" / "backups"
RESTORE_ROOT = SCRIPT_DIRECTORY.parents[1] / ".tmp" / "restore-rehearsal"


class RecoverySetError(RuntimeError):
    """A safe error for an incomplete paired recovery check."""


def _load_script(name: str, filename: str) -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        name, SCRIPT_DIRECTORY / filename
    )
    if specification is None or specification.loader is None:
        raise RecoverySetError("恢复组件不可用")
    loaded = importlib.util.module_from_spec(specification)
    sys.modules[name] = loaded
    specification.loader.exec_module(loaded)
    return loaded


DATABASE_RESTORE = _load_script("tgo_database_restore", "restore-rehearsal.py")
OBJECT_BACKUP = _load_script("tgo_object_backup", "backup-local-objects.py")


def _created_at(manifest: Path, field: str) -> datetime:
    try:
        value = json.loads(manifest.read_text(encoding="utf-8-sig"))[field]
        created = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RecoverySetError("备份创建时间无效") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise RecoverySetError("备份创建时间缺少时区")
    return created


def assess_recovery_set(
    database_manifest: Path,
    objects_manifest: Path,
    *,
    backup_root: Path = BACKUP_ROOT,
    rehearse: bool = False,
    restore_root: Path = RESTORE_ROOT,
    compose: object | None = None,
) -> dict[str, object]:
    """Require both verified archives; rehearse only in disposable targets."""
    database = DATABASE_RESTORE.verify_backup(database_manifest, backup_root)
    objects = OBJECT_BACKUP.verify_backup(objects_manifest, backup_root)
    gap = abs(
        _created_at(database_manifest, "createdAtUtc")
        - _created_at(objects_manifest, "created_at_utc")
    )
    report: dict[str, object] = {
        "database_verified": True,
        "objects_verified": True,
        "database_sha256": database.sha256,
        "object_file_count": objects.file_count,
        "object_total_bytes": objects.total_bytes,
        "backup_time_gap_seconds": int(gap.total_seconds()),
        "point_in_time_consistency": "unverified",
        "database_restore_performed": False,
        "objects_restore_performed": False,
        "complete_rehearsal": False,
    }
    if not rehearse:
        return report

    database_result = DATABASE_RESTORE.restore_rehearsal(
        database, compose if compose is not None else DATABASE_RESTORE.LocalCompose()
    )
    if database_result.get("restore_performed") is not True:
        raise RecoverySetError("数据库隔离恢复没有完成")
    objects_result = OBJECT_BACKUP.rehearse_restore(objects, restore_root)
    if objects_result.get("restore_performed") is not True:
        raise RecoverySetError("对象文件隔离恢复没有完成")
    report.update({
        "database_restore_performed": True,
        "objects_restore_performed": True,
        "complete_rehearsal": True,
        "restored_row_counts": database_result["restored_row_counts"],
        "restored_object_files": objects_result["restored_files"],
        "restored_object_bytes": objects_result["restored_bytes"],
    })
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="联合校验数据库与本地对象备份")
    parser.add_argument("--database-manifest", required=True, type=Path)
    parser.add_argument("--objects-manifest", required=True, type=Path)
    parser.add_argument(
        "--rehearse", action="store_true",
        help="在隔离数据库和临时目录中实际恢复两个备份",
    )
    arguments = parser.parse_args(argv)
    try:
        report = assess_recovery_set(
            arguments.database_manifest,
            arguments.objects_manifest,
            rehearse=arguments.rehearse,
        )
    except (
        RecoverySetError,
        DATABASE_RESTORE.RehearsalError,
        OBJECT_BACKUP.BackupError,
    ) as exc:
        print(f"联合恢复检查失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

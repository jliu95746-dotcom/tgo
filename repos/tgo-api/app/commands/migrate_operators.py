"""Apply only the independent-operator migration after checking a backup."""

import argparse
import sys
from pathlib import Path

from app.services.operator_migration import (
    apply_operator_revision,
    validate_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="域见运营账号结构迁移")
    parser.add_argument("--backup-manifest", required=True, type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="提交 0037 迁移；未指定时演练后回滚",
    )
    args = parser.parse_args()
    try:
        validate_backup(args.backup_manifest)
        from app.core.database import sync_engine

        with sync_engine.connect() as connection:
            transaction = connection.begin()
            try:
                changed = apply_operator_revision(connection)
                if args.apply:
                    transaction.commit()
                else:
                    transaction.rollback()
            except Exception:
                transaction.rollback()
                raise
    except Exception as exc:
        sys.stderr.write(
            f"运营迁移未完成（{type(exc).__name__}）。" "请检查备份及版本；未输出连接或文件内容。\n"
        )
        return 1
    if not changed:
        sys.stdout.write("PASS: 0037 已应用，本次未新增结构。\n")
    elif args.apply:
        sys.stdout.write("PASS: 已提交 0037；未创建账号或开启 SaaS/收费。\n")
    else:
        sys.stdout.write("PASS: 0037 演练通过并已回滚；主库版本未变。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Inspect historical attachments without changing database or file content."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.chat_file_audit import (
    AttachmentAuditReport,
    audit_chat_files,
)


def run_audit(
    db: Session,
    *,
    storage_type: str,
    upload_root: Path,
    sample_limit: int,
) -> AttachmentAuditReport:
    """Require a fresh PostgreSQL transaction and always roll it back."""
    if db.in_transaction():
        raise ValueError("A fresh database session is required")
    if db.get_bind().dialect.name != "postgresql":
        raise ValueError("The maintenance command requires PostgreSQL")
    try:
        db.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        db.execute(text("SET LOCAL statement_timeout = '30s'"))
        return audit_chat_files(
            db,
            storage_type=storage_type,
            upload_root=upload_root,
            sample_limit=sample_limit,
        )
    finally:
        db.rollback()


def main() -> int:
    parser = argparse.ArgumentParser(description="域见历史聊天附件只读核对")
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=20,
        choices=range(0, 101),
        metavar="0..100",
        help="异常记录样本数量；不限制完整统计",
    )
    args = parser.parse_args()
    from app.core.config import settings
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            report = run_audit(
                db,
                storage_type=settings.STORAGE_TYPE,
                upload_root=Path(settings.UPLOAD_BASE_DIR),
                sample_limit=args.sample_limit,
            )
    except Exception as exc:
        # Connection errors may include passwords or hostnames.
        sys.stderr.write(f"附件核对失败（{type(exc).__name__}），未输出连接或文件信息。\n")
        return 1
    sys.stdout.write(
        json.dumps(
            asdict(report),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        + "\n"
    )
    return 2 if report.flagged_records else 0


if __name__ == "__main__":
    raise SystemExit(main())

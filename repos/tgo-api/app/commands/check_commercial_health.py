"""Exit 0: no backlog signals; 2: attention required; 1: check unavailable."""

import argparse
import sys

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.commercial_health import (
    CommercialHealthReport,
    inspect_commercial_health,
)


def run_check(db: Session) -> CommercialHealthReport:
    if db.in_transaction():
        raise ValueError("A fresh database session is required")
    if db.get_bind().dialect.name != "postgresql":
        raise ValueError("The maintenance command requires PostgreSQL")
    try:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        db.execute(text("SET LOCAL statement_timeout = '30s'"))
        return inspect_commercial_health(db)
    finally:
        db.rollback()


def main() -> int:
    argparse.ArgumentParser(description="域见商业化只读监测：开通、任务、额度预占及对账异常数量").parse_args()
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            report = run_check(db)
    except Exception as exc:
        sys.stderr.write(f"商业化监测未完成（{type(exc).__name__}）；请检查数据库连接和迁移版本。\n")
        return 1
    sys.stdout.write(report.model_dump_json(indent=2) + "\n")
    return 2 if report.status == "attention" else 0


if __name__ == "__main__":
    raise SystemExit(main())

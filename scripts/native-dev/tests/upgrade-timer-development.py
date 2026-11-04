"""Apply the rehearsed additive timer migration to the local development DB."""

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).with_name("workflow-timer-migration-rehearsal.py")
SPEC = importlib.util.spec_from_file_location(
    "development_timer_upgrade", SOURCE
)
assert SPEC is not None and SPEC.loader is not None
rehearsal = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rehearsal
SPEC.loader.exec_module(rehearsal)


async def apply(manifest: Path) -> dict[str, object]:
    if os.environ.get("ENVIRONMENT") != "development":
        raise ValueError("Explicit development environment required")
    target = make_url(rehearsal.settings.DATABASE_URL)
    if target.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only loopback development PostgreSQL is permitted")
    if (target.database or "").startswith(
        ("tgo_restore_", "tgo_scenarios_test_")
    ):
        raise ValueError("This command requires the configured development DB")
    assert not rehearsal.settings.TIMER_EXECUTION_ENABLED
    restore = rehearsal.load_restore_module()
    backup = restore.verify_backup(manifest)
    proof = json.loads(
        (
            ROOT / ".tmp/native-dev/"
            "workflow-timer-migration-rehearsal-report.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        proof["verification_completed"] and proof["source_version_unchanged"]
    )
    assert (
        proof["isolated_database_removed"]
        and proof["temporary_archive_removed"]
    )
    assert proof["backup_sha256"] == backup.sha256
    evidence = proof["migration_evidence"]
    assert (
        evidence["source_rollback_guard"]
        and evidence["suspension_rollback_guard"]
    )
    assert (
        evidence["command_rollback_guard"]
        and evidence["original_records_preserved"]
    )
    assert evidence["empty_rollback_and_reupgrade"]
    engine = create_async_engine(target, hide_parameters=True)
    try:
        async with engine.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout='3s'"))
            await db.execute(text("SET LOCAL statement_timeout='30s'"))
            assert await rehearsal.version(db) == rehearsal.OLDER
            tables = await rehearsal.fingerprints(db)
            assert not (set(tables) & rehearsal.NEW_TABLES)
            # Serialize the short additive transaction against other writers.
            for name in sorted(tables):
                await db.execute(
                    text(
                        'LOCK TABLE public."'
                        + name
                        + '" IN SHARE ROW EXCLUSIVE MODE'
                    )
                )
            original = await rehearsal.fingerprints(db)
            active = await db.scalar(
                text(
                    "SELECT count(*) FROM wf_workflow_executions "
                    "WHERE status IN ('pending','running')",
                )
            )
            assert active == 0
            before_api = await db.scalar(
                text("SELECT version_num FROM api_alembic_version")
            )
            await db.run_sync(
                lambda sync: rehearsal.migrate(sync, rehearsal.NEWER)
            )
            assert await rehearsal.version(db) == rehearsal.NEWER
            current = await rehearsal.fingerprints(db)
            assert {name: current[name] for name in original} == original
            assert all(
                current[name].rows == 0 for name in rehearsal.NEW_TABLES
            )
            assert (
                await db.scalar(
                    text("SELECT version_num FROM api_alembic_version")
                )
                == before_api
            )
        return {
            "migration_applied": True,
            "from_version": rehearsal.OLDER,
            "to_version": rehearsal.NEWER,
            "backup_sha256": backup.sha256,
            "original_records_preserved": True,
            "new_tables_empty": True,
            "original_table_count": len(original),
            "original_row_count": sum(item.rows for item in original.values()),
            "api_version_unchanged": True,
            "timer_enabled": False,
        }
    finally:
        await engine.dispose()


def main() -> bool:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply-development", action="store_true")
    args = parser.parse_args()
    if not args.apply_development or not __debug__:
        parser.error("Explicit --apply-development with assertions required")
    report: dict[str, object] = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "migration_applied": False,
    }
    try:
        report.update(asyncio.run(apply(args.manifest)))
    except Exception as error:
        report["error_type"] = type(error).__name__
        report["commit_state_requires_readback"] = True
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    (
        ROOT / ".tmp/native-dev/workflow-timer-development-upgrade-report.json"
    ).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return report["migration_applied"] is True


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    raise SystemExit(0 if main() else 1)

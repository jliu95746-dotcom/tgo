"""Upgrade and guard rollback of a restored copy, never the source database."""

import argparse
import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import logging
from pathlib import Path
import re
import sys
from typing import NamedTuple, TypedDict
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import JsonValue
from sqlalchemy import delete, text
from sqlalchemy.engine import Connection, URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    create_async_engine,
)

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402
from app.models.workflow import Workflow  # noqa: E402
from app.models.workflow_release import WorkflowRelease  # noqa: E402
from app.models.workflow_schedule import (  # noqa: E402
    WorkflowSchedule,
    WorkflowScheduleCommand,
)
from app.models.workflow_timer_occurrence import (  # noqa: E402
    WorkflowTimerOccurrence,
)
from app.schemas.workflow_schedule import ScheduleSummary  # noqa: E402
from app.services.execution_snapshot import definition_digest  # noqa: E402

OLDER = "0017_event_receipt_keys"
NEWER = "0019_timer_occurrences"
SCHEDULES = "0018_workflow_schedules"
NEW_TABLES = {
    "wf_workflow_schedules",
    "wf_workflow_schedule_commands",
    "wf_timer_occurrences",
}


class Fingerprint(NamedTuple):
    rows: int
    sha256: str


class MigrationEvidence(TypedDict, total=False):
    phase: str
    isolated_database: str
    original_table_count: int
    original_row_count: int
    original_records_preserved: bool
    empty_rollback_and_reupgrade: bool
    source_rollback_guard: bool
    suspension_rollback_guard: bool
    command_rollback_guard: bool
    final_version: str


def owned_url(source: str, database: str) -> URL:
    url = make_url(source)
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only loopback PostgreSQL is permitted")
    if not re.fullmatch(r"tgo_restore_[a-f0-9]{32}", database):
        raise ValueError("A generated restore database is required")
    if database == url.database:
        raise ValueError("The source database cannot be migrated")
    return url.set(database=database)


async def fingerprints(db: AsyncConnection) -> dict[str, Fingerprint]:
    tables = await db.scalars(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'",
        )
    )
    result: dict[str, Fingerprint] = {}
    for table in sorted(name for name in tables if name.startswith("wf_")):
        if not re.fullmatch(r"wf_[a-z0-9_]+", table):
            raise ValueError("Unexpected Workflow table identifier")
        # Hash content in PostgreSQL without transferring customer rows.
        row = (
            await db.execute(
                text(
                    "SELECT count(*), encode(sha256(convert_to(COALESCE("
                    "string_agg(to_jsonb(t)::text, E'\\n' "
                    "ORDER BY to_jsonb(t)::text),"
                    "''),'UTF8')),'hex') FROM public.\"" + table + '" t',
                )
            )
        ).one()
        assert type(row[0]) is int and isinstance(row[1], str)
        assert re.fullmatch(r"[a-f0-9]{64}", row[1])
        result[table] = Fingerprint(row[0], row[1])
    return result


async def version(db: AsyncConnection) -> str:
    value = await db.scalar(text("SELECT version_num FROM alembic_version"))
    assert isinstance(value, str)
    return value


def migrate(db: Connection, target: str, *, downgrade: bool = False) -> None:
    config = Config(str(SERVICE / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE / "migrations"))
    config.attributes["connection"] = db
    (command.downgrade if downgrade else command.upgrade)(config, target)


async def refuse_rollback(
    db: AsyncConnection,
    target: str,
    expected: str,
) -> None:
    await db.commit()
    try:
        await db.run_sync(lambda sync: migrate(sync, target, downgrade=True))
    except RuntimeError as error:
        await db.rollback()
        if expected not in str(error):
            raise
    else:
        raise AssertionError("Nonempty timer history was not protected")


async def verify_copy(database: str, evidence: MigrationEvidence) -> None:
    engine = create_async_engine(
        owned_url(settings.DATABASE_URL, database), hide_parameters=True
    )
    evidence["phase"] = "checking_restored_copy"
    evidence["isolated_database"] = database
    try:
        async with engine.connect() as db:
            assert (
                await db.scalar(text("SELECT current_database()")) == database
            )
            assert await version(db) == OLDER
            original = await fingerprints(db)
            assert original and not (set(original) & NEW_TABLES)
            evidence["original_table_count"] = len(original)
            evidence["original_row_count"] = sum(
                item.rows for item in original.values()
            )
            await db.commit()
            evidence["phase"] = "empty_upgrade_and_rollback"
            await db.run_sync(lambda sync: migrate(sync, NEWER))
            await db.commit()
            assert await version(db) == NEWER
            upgraded = await fingerprints(db)
            assert set(upgraded) == set(original) | NEW_TABLES
            assert all(upgraded[name].rows == 0 for name in NEW_TABLES)
            assert {name: upgraded[name] for name in original} == original
            await db.commit()
            await db.run_sync(
                lambda sync: migrate(sync, OLDER, downgrade=True)
            )
            await db.commit()
            assert await version(db) == OLDER
            assert await fingerprints(db) == original
            await db.commit()
            await db.run_sync(lambda sync: migrate(sync, NEWER))
            await db.commit()
            evidence["empty_rollback_and_reupgrade"] = True

        evidence["phase"] = "nonempty_rollback_guards"
        flow, release, plan_id, project, actor = (
            str(uuid4()) for _ in range(5)
        )
        due = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        graph: dict[str, JsonValue] = {"nodes": [], "edges": []}
        digest = definition_digest(graph)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert await db.get(Workflow, flow) is None
            db.add(
                Workflow(
                    id=flow,
                    project_id=project,
                    name="定时迁移预演合成记录",
                    definition=graph,
                )
            )
            await db.flush()
            db.add(
                WorkflowRelease(
                    id=release,
                    workflow_id=flow,
                    project_id=project,
                    version=1,
                    draft_version=1,
                    definition=graph,
                    definition_digest=digest,
                )
            )
            await db.flush()
            plan = WorkflowSchedule(
                id=plan_id,
                project_id=project,
                workflow_id=flow,
                revision=1,
                enabled=False,
                release_id=release,
                release_digest=digest,
                cron_expression="0 9 * * *",
                timezone="UTC",
                created_by=actor,
                updated_by=actor,
                created_at=due,
                updated_at=due,
            )
            db.add(plan)
            await db.flush()
            db.add(
                WorkflowScheduleCommand(
                    project_id=project,
                    request_id=str(uuid4()),
                    workflow_id=flow,
                    actor_staff_id=actor,
                    operation="save",
                    request_digest=digest,
                    result_snapshot=ScheduleSummary.model_validate(
                        plan
                    ).model_dump(mode="json"),
                )
            )
            db.add(
                WorkflowTimerOccurrence(
                    id=str(uuid4()),
                    project_id=project,
                    workflow_id=flow,
                    schedule_id=plan_id,
                    revision=1,
                    scheduled_at=due,
                    actor_staff_id=actor,
                    release_id=release,
                    release_digest=digest,
                    status="skipped",
                    attempts=0,
                    next_attempt_at=due,
                    created_at=due,
                    updated_at=due,
                )
            )
            await db.commit()

        async with engine.connect() as db:
            before_guard = await fingerprints(db)
            await refuse_rollback(db, SCHEDULES, "已有定时来源")
            assert await version(db) == NEWER
            assert await fingerprints(db) == before_guard
            evidence["source_rollback_guard"] = True
            await db.execute(
                delete(WorkflowTimerOccurrence).where(
                    WorkflowTimerOccurrence.schedule_id == plan_id,
                    WorkflowTimerOccurrence.project_id == project,
                )
            )
            await db.commit()
            await db.execute(
                text(
                    "UPDATE wf_workflow_schedules SET "
                    "suspension_reason='synthetic_rehearsal_pause',"
                    "suspended_at=:due "
                    "WHERE id=:plan AND project_id=:project",
                ),
                {"due": due, "plan": plan_id, "project": project},
            )
            await db.commit()
            before_pause_guard = await fingerprints(db)
            await refuse_rollback(db, SCHEDULES, "已有定时来源")
            assert await version(db) == NEWER
            assert await fingerprints(db) == before_pause_guard
            evidence["suspension_rollback_guard"] = True
            await db.execute(
                text(
                    "UPDATE wf_workflow_schedules SET "
                    "suspension_reason=NULL,suspended_at=NULL "
                    "WHERE id=:plan AND project_id=:project",
                ),
                {"plan": plan_id, "project": project},
            )
            await db.commit()
            await db.run_sync(
                lambda sync: migrate(sync, SCHEDULES, downgrade=True)
            )
            await db.commit()
            before_guard = await fingerprints(db)
            await refuse_rollback(db, OLDER, "已有定时计划或请求回执")
            assert await version(db) == SCHEDULES
            assert await fingerprints(db) == before_guard
            evidence["command_rollback_guard"] = True
            await db.commit()
            await db.run_sync(lambda sync: migrate(sync, NEWER))
            await db.commit()
            await db.execute(
                delete(Workflow).where(
                    Workflow.id == flow,
                    Workflow.project_id == project,
                )
            )
            await db.commit()
            final = await fingerprints(db)
            assert {name: final[name] for name in original} == original
            assert all(final[name].rows == 0 for name in NEW_TABLES)
            assert await version(db) == NEWER
            evidence["original_records_preserved"] = True
            evidence["final_version"] = NEWER
            evidence["phase"] = "copy_verified"
    finally:
        await engine.dispose()


def load_restore_module():
    spec = importlib.util.spec_from_file_location(
        "timer_restore_rehearsal",
        ROOT / "scripts/native-dev/restore-rehearsal.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def source_version() -> str:
    url = make_url(settings.DATABASE_URL)
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Only loopback source metadata may be read")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as db:
            return await version(db)
    finally:
        await engine.dispose()


def main() -> bool:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--run-local", action="store_true")
    args = parser.parse_args()
    if not args.run_local or not __debug__:
        parser.error(
            "Explicit --run-local and enabled assertions are required"
        )
    module = load_restore_module()
    evidence: MigrationEvidence = {}
    report: dict[str, object] = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "verification_completed": False,
        "migration_evidence": evidence,
    }
    try:
        initial = asyncio.run(source_version())
        backup = module.verify_backup(args.manifest)
        result = module.restore_rehearsal(
            backup,
            module.LocalCompose(),
            verify_restored=lambda name: asyncio.run(
                verify_copy(name, evidence)
            ),
        )
        report.update(result)
        current = asyncio.run(source_version())
        assert initial == current
        report.update(
            source_version_before=initial,
            source_version_after=current,
            source_version_unchanged=True,
            verification_completed=True,
        )
    except Exception as error:
        report["error_type"] = type(error).__name__
        if error.__cause__ is not None:
            report["cause_type"] = type(error.__cause__).__name__
    report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    target = (
        ROOT / ".tmp/native-dev/workflow-timer-migration-rehearsal-report.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return report["verification_completed"] is True


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    raise SystemExit(0 if main() else 1)

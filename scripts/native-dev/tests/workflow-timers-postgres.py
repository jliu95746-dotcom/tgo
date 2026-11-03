"""Run timer or webhook tests in a newly owned loopback database."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import NotRequired, TypedDict
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402


class TimerPostgresReport(TypedDict):
    started_at_utc: str
    database: str
    test_exit: int | None
    passed: int
    verification_completed: bool
    owned_database_removed: bool
    logs_contain_connection_password: bool
    error_type: NotRequired[str]
    cleanup_error_type: NotRequired[str]
    checked_at_utc: NotRequired[str]


async def run(suite: str = "timer") -> bool:
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    url = make_url(settings.DATABASE_URL)
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Only loopback PostgreSQL is permitted")
    identity = uuid4().hex
    if suite not in {"timer", "webhook"}:
        raise ValueError("Unknown PostgreSQL verification suite")
    database = "tgo_scenarios_test_" + suite + "_" + identity
    assert re.fullmatch(
        r"tgo_scenarios_test_(timer|webhook)_[a-f0-9]{32}", database
    )
    directory = ROOT / ".tmp/native-dev" / (suite + "-postgres-" + identity)
    directory.mkdir(parents=True, exist_ok=False)
    report: TimerPostgresReport = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "test_exit": None,
        "passed": 0,
        "verification_completed": False,
        "owned_database_removed": False,
        "logs_contain_connection_password": False,
    }
    admin = create_async_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created = False
    try:
        async with admin.connect() as connection:
            assert not await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": database},
            )
            await connection.execute(
                text(
                    'CREATE DATABASE "' + database + '"',
                )
            )
            created = True
        environment = os.environ.copy()
        target = url.set(database=database).render_as_string(
            hide_password=False,
        )
        environment.update(
            {
                "DATABASE_URL": target,
                "WORKFLOW_POSTGRES_TEST_URL": target,
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONUTF8": "1",
            }
        )
        tests = (
            [
                "tests/integration/test_workflow_schedules_postgres.py",
                "tests/integration/test_timer_recovery_postgres.py",
            ]
            if suite == "timer"
            else ["tests/integration/test_workflow_webhooks_postgres.py",
                  "tests/integration/test_webhook_delivery_postgres.py"]
        )
        result = await asyncio.to_thread(
            subprocess.run,
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "pytest_asyncio.plugin",
                *tests,
                "-q",
                "--tb=short",
                "--disable-warnings",
                "--basetemp=" + str(directory / "pytest"),
            ],
            cwd=SERVICE,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=360 if suite == "webhook" else 240,
        )
        contents = result.stdout + result.stderr
        password = url.password
        if password and password in contents:
            report["logs_contain_connection_password"] = True
            contents = contents.replace(password, "[REDACTED]")
        (directory / "pytest.log").write_text(contents, encoding="utf-8")
        report["test_exit"] = result.returncode
        matched = re.search(r"(\d+) passed", contents)
        report["passed"] = int(matched[1]) if matched else 0
        report["verification_completed"] = bool(
            result.returncode == 0
            and report["passed"] >= (13 if suite == "timer" else 14)
            and not re.search(r"\d+ skipped", contents)
            and not report["logs_contain_connection_password"]
        )
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execute(
                        text(
                            'DROP DATABASE "' + database + '" WITH (FORCE)',
                        )
                    )
                    report["owned_database_removed"] = not bool(
                        await connection.scalar(
                            text(
                                "SELECT 1 FROM pg_database "
                                "WHERE datname=:name",
                            ),
                            {"name": database},
                        )
                    )
        except Exception as error:
            report["cleanup_error_type"] = type(error).__name__
        await admin.dispose()
        report["verification_completed"] = bool(
            report["verification_completed"]
            and report["owned_database_removed"],
        )
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        summary = json.dumps(report, ensure_ascii=False, indent=2)
        (directory / "report.json").write_text(summary, encoding="utf-8")
        report_path = ROOT / (
            ".tmp/native-dev/workflow-" + suite + "-postgres-report.json"
        )
        report_path.write_text(
            summary,
            encoding="utf-8",
        )
        print(summary)
    return bool(report["verification_completed"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument(
        "--suite", choices=("timer", "webhook"), default="timer"
    )
    args = parser.parse_args()
    if not args.run_local:
        parser.error("Explicit --run-local is required")
    logging.disable(logging.CRITICAL)
    raise SystemExit(0 if asyncio.run(run(args.suite)) else 1)

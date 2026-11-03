"""Run disposable Webhook checks without changing native business data."""

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
from uuid import uuid4
from urllib.parse import quote, quote_plus
import xml.etree.ElementTree as ET

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine


ROOT = Path(__file__).resolve().parents[3]
SERVICE = ROOT / "repos/tgo-workflow"
REQUIRED_CASES = {
    "test_real_private_http_registration_rotation_and_recovery",
    "test_public_http_durable_recovery_worker_restart_and_revocation",
    "test_formal_staff_login_without_test_token_endpoint",
}
sys.path.insert(0, str(SERVICE))
from app.config import settings  # noqa: E402


def require_local() -> None:
    if not __debug__:
        raise RuntimeError("Python assertions must remain enabled")
    for target in (settings.DATABASE_URL, settings.REDIS_URL):
        if make_url(target).host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("Only loopback infrastructure is allowed")


def redact_connection_secrets(content: str, url: URL) -> tuple[str, bool]:
    password = url.password
    if not password:
        return content, False
    variants = {password, quote(password, safe=""), quote_plus(password)}
    detected = False
    # Short development passwords can also be ordinary project path segments.
    # Match those only in URL userinfo or explicit password fields.
    if len(password) >= 16 and password in content:
        content = content.replace(password, "[REDACTED]")
        detected = True
    value = "(?:" + "|".join(
        re.escape(item) for item in sorted(variants, key=len, reverse=True)
    ) + ")"
    patterns = (
        r"(?P<prefix>:)" + value + r"(?=@)",
        r"(?P<prefix>\b(?:[a-z_]*password|passwd|pwd)[\"']?"
        r"\s*[:=]\s*[\"']?)" + value + r"(?=$|[\s,\"';}])",
    )
    for pattern in patterns:
        content, count = re.subn(
            pattern,
            lambda match: match.group("prefix") + "[REDACTED]",
            content,
            flags=re.IGNORECASE,
        )
        detected = detected or count > 0
    return content, detected


async def main() -> int:
    require_local()
    logging.disable(logging.CRITICAL)
    identity = uuid4().hex
    database = "tgo_scenarios_test_webhook_" + identity
    if not re.fullmatch(r"tgo_scenarios_test_webhook_[a-f0-9]{32}", database):
        raise RuntimeError("An owned database name is required")
    directory = ROOT / ".tmp/native-dev" / ("webhook-live-" + identity)
    directory.mkdir(parents=True, exist_ok=False)
    report_path = ROOT / ".tmp/native-dev/workflow-webhooks-report.json"
    report = {
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "run_directory": str(directory),
        "verification_completed": False,
        "test_exit": None,
        "passed": 0,
        "skipped": 0,
        "required_runtime_cases_passed": False,
        "owned_database_removed": False,
        "logs_contain_connection_password": False,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (directory / "verification-report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    url = make_url(settings.DATABASE_URL)
    admin = create_async_engine(
        url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    created = False
    failure = None
    try:
        async with admin.connect() as connection:
            if await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname=:name"),
                {"name": database},
            ):
                raise RuntimeError("Owned test database already exists")
            await connection.execute(text(f'CREATE DATABASE "{database}"'))
            created = True
        environment = os.environ.copy()
        target = url.set(database=database).render_as_string(
            hide_password=False
        )
        environment.update(
            DATABASE_URL=target,
            WORKFLOW_POSTGRES_TEST_URL=target,
            DATABASE_ECHO="false",
            PYTHONUTF8="1",
        )
        junit = directory / "results.xml"
        log_path = directory / "pytest.log"
        with log_path.open("wb") as log:
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable, "-m", "pytest",
                    "tests/integration/test_workflow_webhooks_postgres.py",
                    "tests/integration/test_webhook_delivery_postgres.py",
                    "tests/integration/test_webhook_staff_login_http.py",
                    "-q", "--tb=short", "--junitxml=" + str(junit),
                    "--basetemp=" + str(directory / "pytest-temp"),
                ],
                cwd=SERVICE, env=environment, stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        report["test_exit"] = result.returncode
        if junit.exists():
            cases = list(ET.parse(junit).getroot().iter("testcase"))
            report["skipped"] = sum(
                case.find("skipped") is not None for case in cases
            )
            passed_cases = [
                case for case in cases if not any(
                    case.find(tag) is not None
                    for tag in ("skipped", "failure", "error")
                )
            ]
            report["passed"] = len(passed_cases)
            report["required_runtime_cases_passed"] = REQUIRED_CASES <= {
                case.get("name") for case in passed_cases
            }
        report["verification_completed"] = (
            result.returncode == 0 and report["passed"] >= 15
            and report["skipped"] == 0
            and report["required_runtime_cases_passed"]
        )
    except Exception as error:
        # Diagnostic class only: a connection error may contain credentials.
        failure = type(error).__name__
    finally:
        if created:
            try:
                async with admin.connect() as connection:
                    await connection.execute(
                        text(f'DROP DATABASE "{database}" WITH (FORCE)')
                    )
                    report["owned_database_removed"] = not bool(
                        await connection.scalar(
                            text(
                                "SELECT 1 FROM pg_database WHERE datname=:name"
                            ),
                            {"name": database},
                        )
                    )
            except Exception as error:
                report["cleanup_error"] = type(error).__name__
        await admin.dispose()
        if url.password:
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix in {".log", ".xml"}:
                    content = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    cleaned, detected = redact_connection_secrets(content, url)
                    if detected:
                        report["logs_contain_connection_password"] = True
                        path.write_text(cleaned, encoding="utf-8")
        if failure:
            report["error_class"] = failure
        report["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
        report["verification_completed"] = bool(
            report["verification_completed"]
            and report["owned_database_removed"]
            and not report["logs_contain_connection_password"]
        )
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        (directory / "verification-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["verification_completed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-local", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(asyncio.run(main()))

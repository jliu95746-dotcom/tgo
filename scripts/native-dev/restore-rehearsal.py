"""Restore a local custom archive into a disposable Compose PostgreSQL database.

This never targets the live application database. It emits counts, not rows.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Sequence
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKUP_ROOT = REPO_ROOT / ".tmp" / "backups"
COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / "docker-compose.native.yml",
)
ENV_FILE = REPO_ROOT / ".env.dev"
KEY_TABLES = (
    "api_staff",
    "api_visitors",
    "ai_agents",
    "api_ai_interaction_runs",
    "api_media_analysis_results",
    "api_message_intent_results",
    "api_assist_draft_records",
    "api_staff_message_deliveries",
    "api_shadow_review_batches",
    "wf_workflows",
    "wf_workflow_executions",
)
_SAFE_DATABASE = re.compile(r"^tgo_restore_[0-9a-f]{32}$")


class RehearsalError(RuntimeError):
    """Safe diagnostic without backup contents or database credentials."""


@dataclass(frozen=True)
class VerifiedBackup:
    path: Path
    sha256: str
    listed_entries: int


def verify_backup(
    manifest_path: Path, backup_root: Path = BACKUP_ROOT
) -> VerifiedBackup:
    root = backup_root.resolve(strict=True)
    manifest_path = manifest_path.resolve(strict=True)
    if not manifest_path.is_relative_to(root) or manifest_path.name != "manifest.json":
        raise RehearsalError("备份清单必须位于本项目 .tmp/backups 目录")
    if manifest_path.stat().st_size > 65536:
        raise RehearsalError("备份清单过大")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        archive = (manifest_path.parent / "database.dump").resolve(strict=True)
        original_path = Path(manifest["backupPath"]).resolve(strict=True)
        expected_size = manifest["bytes"]
        expected_hash = manifest["sha256"]
        listed_entries = manifest["archiveListingLines"]
        datetime.fromisoformat(manifest["createdAtUtc"].replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RehearsalError("备份清单格式无效") from exc
    if (
        archive.parent != manifest_path.parent
        or original_path != archive
        or type(expected_size) is not int
        or expected_size <= 5
        or archive.stat().st_size != expected_size
        or not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash) is None
        or type(listed_entries) is not int
        or listed_entries <= 0
        or manifest.get("archiveListingVerified") is not True
        or manifest.get("migrationApplied") is not False
    ):
        raise RehearsalError("备份清单与归档不一致")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise RehearsalError("归档不是 PostgreSQL custom 格式")
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != expected_hash.lower():
        raise RehearsalError("备份归档 SHA256 校验失败")
    return VerifiedBackup(archive, digest.hexdigest(), listed_entries)


CommandRunner = Callable[[Sequence[str], int], subprocess.CompletedProcess[str]]


def _subprocess_runner(
    args: Sequence[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


class LocalCompose:
    def __init__(
        self,
        *,
        root: Path = REPO_ROOT,
        runner: CommandRunner = _subprocess_runner,
    ) -> None:
        self.root = root
        self.runner = runner
        self.compose_prefix = [
            "docker",
            "compose",
            "--env-file",
            str(root / ".env.dev"),
        ]
        for filename in (
            "docker-compose.yml",
            "docker-compose.dev.yml",
            "docker-compose.native.yml",
        ):
            self.compose_prefix.extend(("-f", str(root / filename)))

    def call(self, args: Sequence[str], *, step: str, timeout: int = 60) -> str:
        try:
            result = self.runner([*self.compose_prefix, *args], timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RehearsalError(f"{step}无法完成") from exc
        if result.returncode != 0:
            raise RehearsalError(f"{step}失败（exit {result.returncode}）")
        return result.stdout

    def local_context(self) -> None:
        override = os.environ.get("DOCKER_HOST", "")
        if override and not override.startswith(("npipe://", "unix://")):
            raise RehearsalError("拒绝在远程 Docker 主机进行恢复演练")
        try:
            result = self.runner(["docker", "context", "inspect"], 15)
            contexts = json.loads(result.stdout)
            host = contexts[0]["Endpoints"]["docker"]["Host"]
        except (
            OSError,
            subprocess.TimeoutExpired,
            ValueError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raise RehearsalError("无法核验 Docker 连接目标") from exc
        if (
            result.returncode != 0
            or not isinstance(host, str)
            or not host.startswith(("npipe://", "unix://"))
        ):
            raise RehearsalError("拒绝在远程 Docker 主机进行恢复演练")
        if not (self.root / ".env.dev").is_file() or any(
            not (self.root / name).is_file()
            for name in (
                "docker-compose.yml",
                "docker-compose.dev.yml",
                "docker-compose.native.yml",
            )
        ):
            raise RehearsalError("本地 Compose 配置不完整")
        self.call(("config", "--quiet"), step="Compose 配置检查")
        if not self.call(("ps", "-q", "postgres"), step="PostgreSQL 容器检查").strip():
            raise RehearsalError("本地 PostgreSQL 容器未运行")

    def postgres(
        self, shell_command: str, *arguments: str, step: str, timeout: int = 60
    ) -> str:
        return self.call(
            (
                "exec",
                "-T",
                "postgres",
                "sh",
                "-c",
                shell_command,
                "restore",
                *arguments,
            ),
            step=step,
            timeout=timeout,
        )


def restore_rehearsal(
    backup: VerifiedBackup,
    compose: LocalCompose,
    *,
    verify_restored: Callable[[str], None] | None = None,
) -> dict[str, object]:
    compose.local_context()
    database = "tgo_restore_" + uuid4().hex
    if _SAFE_DATABASE.fullmatch(database) is None:
        raise RehearsalError("隔离数据库名称无效")
    remote_archive = "/tmp/" + database + ".dump"
    copy_attempted = database_created = False
    counts: dict[str, int] = {}
    primary_error: RehearsalError | None = None
    cleanup_errors: list[str] = []
    try:
        copy_attempted = True
        compose.call(
            ("cp", str(backup.path), "postgres:" + remote_archive),
            step="复制备份归档",
            timeout=120,
        )
        copied_digest = (
            compose.postgres(
                'exec sha256sum -- "$1"',
                remote_archive,
                step="核验容器内备份归档",
                timeout=60,
            )
            .strip()
            .split(maxsplit=1)
        )
        if (
            len(copied_digest) != 2
            or re.fullmatch(r"[0-9a-fA-F]{64}", copied_digest[0]) is None
            or copied_digest[0].lower() != backup.sha256
            or copied_digest[1] != remote_archive
        ):
            raise RehearsalError("容器内备份归档 SHA256 校验失败")
        listing = compose.postgres(
            'exec pg_restore --list "$1"',
            remote_archive,
            step="恢复前归档目录检查",
            timeout=60,
        )
        if len(listing.splitlines()) < backup.listed_entries:
            raise RehearsalError("容器内归档目录与备份清单不一致")
        try:
            compose.postgres(
                'exec createdb --no-password -U "$POSTGRES_USER" --maintenance-db=postgres "$1"',
                database,
                step="创建隔离数据库",
                timeout=60,
            )
        except RehearsalError as exc:
            raise RehearsalError(f"{exc}；请检查隔离库 {database} 是否残留") from exc
        database_created = True
        compose.postgres(
            'exec pg_restore --no-password -U "$POSTGRES_USER" --no-owner --no-acl '
            '--exit-on-error -d "$1" "$2"',
            database,
            remote_archive,
            step="实际恢复",
            timeout=600,
        )
        if verify_restored is not None:
            try:
                verify_restored(database)
            except Exception as exc:
                raise RehearsalError("恢复副本校验失败") from exc
        for table in KEY_TABLES:
            rows = compose.postgres(
                'exec psql --no-password -U "$POSTGRES_USER" -d "$1" -At '
                '-v ON_ERROR_STOP=1 -c "$2"',
                database,
                f"SELECT count(*) FROM public.{table}",
                step=f"读取恢复后的 {table}",
                timeout=60,
            ).strip()
            try:
                counts[table] = int(rows)
            except ValueError as exc:
                raise RehearsalError(f"恢复后的 {table} 计数无效") from exc
    except RehearsalError as exc:
        primary_error = exc
    finally:
        if database_created:
            try:
                compose.postgres(
                    'exec dropdb --no-password -U "$POSTGRES_USER" '
                    '--maintenance-db=postgres --if-exists --force "$1"',
                    database,
                    step="清理隔离数据库",
                    timeout=60,
                )
            except RehearsalError:
                cleanup_errors.append("隔离数据库未能删除：" + database)
        if copy_attempted:
            try:
                compose.postgres(
                    'exec rm -f -- "$1"',
                    remote_archive,
                    step="清理容器内临时归档",
                    timeout=30,
                )
            except RehearsalError:
                cleanup_errors.append("容器内临时归档未能删除：" + remote_archive)
    if cleanup_errors:
        details = "；".join(cleanup_errors)
        if primary_error is not None:
            details = f"{primary_error}；{details}"
        raise RehearsalError(details) from primary_error
    if primary_error is not None:
        raise primary_error
    return {
        "restore_performed": True,
        "isolated_database_removed": True,
        "temporary_archive_removed": True,
        "backup_sha256": backup.sha256,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "restored_row_counts": counts,
        "post_restore_verification_performed": verify_restored is not None,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="隔离恢复当前项目的 PostgreSQL custom 备份")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true", help="只校验归档，不连接数据库")
    args = parser.parse_args(argv)
    try:
        backup = verify_backup(args.manifest)
        if args.verify_only:
            result: dict[str, object] = {
                "backup_verified": True,
                "restore_performed": False,
                "backup_sha256": backup.sha256,
            }
        else:
            result = restore_rehearsal(backup, LocalCompose())
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, RehearsalError) as exc:
        print(f"恢复演练失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""The restore drill may only touch a generated database and temporary archive."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "restore-rehearsal.py"
SPEC = importlib.util.spec_from_file_location("restore_rehearsal", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def backup_fixture(tmp_path: Path):
    backup_dir = tmp_path / "backups" / "local-backup"
    backup_dir.mkdir(parents=True)
    archive = backup_dir / "database.dump"
    archive.write_bytes(b"PGDMP" + b"example backup bytes")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = backup_dir / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "createdAtUtc": "2026-10-15T00:00:00+00:00",
                "backupPath": str(archive),
                "bytes": archive.stat().st_size,
                "sha256": digest,
                "archiveListingVerified": True,
                "archiveListingLines": 2,
                "migrationApplied": False,
            }
        ),
        encoding="utf-8",
    )
    return manifest, archive, digest


def test_backup_validation_rejects_tamper_and_outside_path(tmp_path):
    manifest, archive, digest = backup_fixture(tmp_path)
    checked = module.verify_backup(manifest, tmp_path / "backups")
    assert checked.path == archive and checked.sha256 == digest
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(module.RehearsalError, match="不一致"):
        module.verify_backup(manifest, tmp_path / "backups")
    (tmp_path / "unrelated").mkdir()
    with pytest.raises(module.RehearsalError, match=".tmp/backups"):
        module.verify_backup(manifest, tmp_path / "unrelated")


def test_remote_docker_context_is_rejected_before_compose(tmp_path):
    calls = []

    def runner(args, timeout):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps(
                [
                    {
                        "Endpoints": {"docker": {"Host": "tcp://foreign.example:2376"}},
                    }
                ]
            ),
            "",
        )

    compose = module.LocalCompose(root=tmp_path, runner=runner)
    with pytest.raises(module.RehearsalError, match="远程 Docker"):
        compose.local_context()
    assert calls == [("docker", "context", "inspect")]


class FakeCompose:
    def __init__(
        self,
        *,
        fail_restore=False,
        fail_create=False,
        fail_table=None,
        corrupt_copy=False,
    ):
        self.calls = []
        self.fail_restore = fail_restore
        self.fail_create = fail_create
        self.fail_table = fail_table
        self.corrupt_copy = corrupt_copy
        self.copied_archive_sha256 = None

    def local_context(self):
        self.calls.append(("local_context",))

    def call(self, args, *, step, timeout=60):
        self.calls.append((step, tuple(args)))
        if step == "复制备份归档":
            self.copied_archive_sha256 = hashlib.sha256(
                Path(args[1]).read_bytes()
            ).hexdigest()
            if self.corrupt_copy:
                self.copied_archive_sha256 = "0" * 64
        return ""

    def postgres(self, shell_command, *arguments, step, timeout=60):
        self.calls.append((step, shell_command, arguments))
        if step == "核验容器内备份归档":
            return f"{self.copied_archive_sha256}  {arguments[0]}\n"
        if step == "恢复前归档目录检查":
            return "header\nTABLE public.api_staff\n"
        if step == "创建隔离数据库" and self.fail_create:
            raise module.RehearsalError("创建隔离数据库失败")
        if step == "实际恢复" and self.fail_restore:
            raise module.RehearsalError("实际恢复失败")
        if step == f"读取恢复后的 {self.fail_table}":
            raise module.RehearsalError("恢复后的业务表读取失败")
        if step.startswith("读取恢复后的"):
            return "3\n"
        return ""


@pytest.mark.parametrize("fail_restore", [False, True])
def test_drill_cleans_only_its_generated_database_and_archive(tmp_path, fail_restore):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose(fail_restore=fail_restore)
    if fail_restore:
        with pytest.raises(module.RehearsalError, match="实际恢复失败"):
            module.restore_rehearsal(backup, compose)
    else:
        result = module.restore_rehearsal(backup, compose)
        assert result["restore_performed"] is True
        assert result["isolated_database_removed"] is True
        assert result["restored_row_counts"] == {
            table: 3 for table in module.KEY_TABLES
        }
    created = next(call for call in compose.calls if call[0] == "创建隔离数据库")
    cleaned = next(call for call in compose.calls if call[0] == "清理隔离数据库")
    database = created[2][0]
    assert database.startswith("tgo_restore_") and len(database) == 44
    assert cleaned[2][0] == database
    digest_step = next(
        index for index, call in enumerate(compose.calls) if call[0] == "核验容器内备份归档"
    )
    creation_step = next(
        index for index, call in enumerate(compose.calls) if call[0] == "创建隔离数据库"
    )
    assert digest_step < creation_step
    assert any(call[0] == "清理容器内临时归档" for call in compose.calls)
    assert all("DROP DATABASE" not in str(call) for call in compose.calls)


def test_corrupted_container_copy_fails_before_creating_database(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose(corrupt_copy=True)

    with pytest.raises(module.RehearsalError, match="SHA256"):
        module.restore_rehearsal(backup, compose)

    assert not any(call[0] == "创建隔离数据库" for call in compose.calls)
    assert any(call[0] == "清理容器内临时归档" for call in compose.calls)


def test_drill_reads_restored_customer_service_business_tables(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose()

    result = module.restore_rehearsal(backup, compose)

    required = {
        "api_ai_interaction_runs",
        "api_media_analysis_results",
        "api_message_intent_results",
        "api_assist_draft_records",
        "api_staff_message_deliveries",
        "api_shadow_review_batches",
        "wf_workflow_executions",
    }
    assert required.issubset(result["restored_row_counts"])
    reads = {
        call[0].removeprefix("读取恢复后的 ")
        for call in compose.calls
        if call[0].startswith("读取恢复后的 ")
    }
    assert required.issubset(reads)


def test_missing_business_table_fails_and_cleans_isolated_database(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose(fail_table="api_message_intent_results")

    with pytest.raises(module.RehearsalError, match="恢复后的业务表读取失败"):
        module.restore_rehearsal(backup, compose)

    assert any(call[0] == "清理隔离数据库" for call in compose.calls)
    assert any(call[0] == "清理容器内临时归档" for call in compose.calls)


def test_failed_database_creation_does_not_drop_a_preexisting_name(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose(fail_create=True)
    with pytest.raises(module.RehearsalError, match="创建隔离数据库失败"):
        module.restore_rehearsal(backup, compose)
    assert all(call[0] != "清理隔离数据库" for call in compose.calls)
    assert any(call[0] == "清理容器内临时归档" for call in compose.calls)


@pytest.mark.parametrize("fail_restore", [False, True])
def test_cleanup_failure_cannot_report_restore_success(tmp_path, fail_restore):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")

    class CleanupFailure(FakeCompose):
        def postgres(self, shell_command, *arguments, step, timeout=60):
            if step == "清理隔离数据库":
                raise module.RehearsalError("cleanup failed")
            return super().postgres(
                shell_command, *arguments, step=step, timeout=timeout
            )

    with pytest.raises(module.RehearsalError, match="隔离数据库未能删除") as error:
        module.restore_rehearsal(backup, CleanupFailure(fail_restore=fail_restore))
    if fail_restore:
        assert "实际恢复失败" in str(error.value)


def test_restored_database_verifier_receives_only_owned_database(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose()

    def verify(database):
        compose.calls.append(("verify_restored", database))

    result = module.restore_rehearsal(backup, compose, verify_restored=verify)
    steps = [call[0] for call in compose.calls]
    verified = next(call for call in compose.calls if call[0] == "verify_restored")
    created = next(call for call in compose.calls if call[0] == "创建隔离数据库")
    assert verified[1] == created[2][0]
    assert steps.index("实际恢复") < steps.index("verify_restored")
    assert steps.index("verify_restored") < steps.index("读取恢复后的 api_staff")
    assert result["post_restore_verification_performed"]


def test_verifier_failure_is_safe_and_still_cleans_owned_resources(tmp_path):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose()

    def verify(database):
        raise ValueError("synthetic-private-connection")

    with pytest.raises(module.RehearsalError, match="恢复副本校验失败") as error:
        module.restore_rehearsal(backup, compose, verify_restored=verify)
    assert "synthetic-private" not in str(error.value)
    steps = [call[0] for call in compose.calls]
    assert "清理隔离数据库" in steps and "清理容器内临时归档" in steps


@pytest.mark.parametrize("failure", ["fail_restore", "corrupt_copy"])
def test_verifier_is_not_called_for_an_unrestored_database(tmp_path, failure):
    manifest, _, _ = backup_fixture(tmp_path)
    backup = module.verify_backup(manifest, tmp_path / "backups")
    compose = FakeCompose(**{failure: True})
    calls = []
    with pytest.raises(module.RehearsalError):
        module.restore_rehearsal(
            backup, compose, verify_restored=lambda database: calls.append(database),
        )
    assert not calls

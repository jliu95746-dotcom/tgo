"""Local object backups stay separate from the database archive."""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "backup-local-objects.py"
SPEC = importlib.util.spec_from_file_location("backup_local_objects", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def local_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    for relative in module.OBJECT_ROOTS:
        (repo / relative).mkdir(parents=True)
    (repo / module.OBJECT_ROOTS[0] / "nested").mkdir()
    (repo / module.OBJECT_ROOTS[0] / "nested" / "one.txt").write_text("customer file")
    (repo / module.OBJECT_ROOTS[1] / "two.bin").write_bytes(b"\x00\x01")
    (repo / ".env.dev").write_text("STORAGE_TYPE=local\n", encoding="utf-8")
    return repo, repo / ".tmp" / "backups"


def test_local_backup_verifies_and_restores_into_disposable_directory(tmp_path):
    repo, backups = local_fixture(tmp_path)
    manifest = module.create_backup(repo, backups)
    checked = module.verify_backup(manifest, backups)
    assert checked.file_count == 2
    assert checked.total_bytes == len(b"customer file") + 2
    result = module.rehearse_restore(checked, repo / ".tmp" / "restore-rehearsal")
    assert result["restore_performed"] is True
    assert result["restored_files"] == 2
    assert not list((repo / ".tmp" / "restore-rehearsal").iterdir())
    assert (
        repo / module.OBJECT_ROOTS[0] / "nested" / "one.txt"
    ).read_text() == "customer file"


def test_changed_archive_and_manifest_traversal_fail_closed(tmp_path):
    repo, backups = local_fixture(tmp_path)
    manifest = module.create_backup(repo, backups)
    stored = manifest.parent / "objects" / module.OBJECT_ROOTS[1] / "two.bin"
    stored.write_bytes(b"changed")
    with pytest.raises(module.BackupError, match="SHA256|大小"):
        module.verify_backup(manifest, backups)

    document = json.loads(manifest.read_text(encoding="utf-8"))
    document["files"][0]["path"] = "../outside"
    manifest.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(module.BackupError, match="路径"):
        module.verify_backup(manifest, backups)


@pytest.mark.parametrize("setting", ["STORAGE_TYPE=minio", "export STORAGE_TYPE = oss"])
def test_remote_storage_mode_is_not_reported_as_local_backup(
    tmp_path, monkeypatch, setting
):
    repo, backups = local_fixture(tmp_path)
    (repo / ".env.dev").write_text(setting + "\n", encoding="utf-8")
    monkeypatch.delenv("STORAGE_TYPE", raising=False)
    with pytest.raises(module.BackupError, match="minio|oss"):
        module.create_backup(repo, backups)
    assert not backups.exists()


def test_symlink_source_is_rejected(tmp_path):
    repo, backups = local_fixture(tmp_path)
    link = repo / module.OBJECT_ROOTS[2] / "shortcut"
    try:
        link.symlink_to(repo / module.OBJECT_ROOTS[0], target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink permission unavailable")
    with pytest.raises(module.BackupError, match="符号链接"):
        module.create_backup(repo, backups)


def test_backup_target_must_remain_in_project_backup_directory(tmp_path):
    repo, _ = local_fixture(tmp_path)
    outside = tmp_path / "outside"
    with pytest.raises(module.BackupError, match="目标目录"):
        module.create_backup(repo, outside)
    assert not outside.exists()

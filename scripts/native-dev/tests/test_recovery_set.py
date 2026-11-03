"""A recovery drill must verify both stores before claiming a complete run."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "recovery-set.py"
SPEC = importlib.util.spec_from_file_location("recovery_set", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def backup_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repo = tmp_path / "repo"
    root = repo / ".tmp" / "backups"
    database_directory = root / "database"
    database_directory.mkdir(parents=True)
    archive = database_directory / "database.dump"
    archive.write_bytes(b"PGDMP" + b"example backup bytes")
    database_manifest = database_directory / "manifest.json"
    database_manifest.write_text(json.dumps({
        "createdAtUtc": "2026-10-15T12:00:00Z",
        "backupPath": str(archive),
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archiveListingVerified": True,
        "archiveListingLines": 2,
        "migrationApplied": False,
    }), encoding="utf-8")
    for relative in module.OBJECT_BACKUP.OBJECT_ROOTS:
        (repo / relative).mkdir(parents=True)
    (repo / module.OBJECT_BACKUP.OBJECT_ROOTS[0] / "photo.bin").write_bytes(
        b"customer media"
    )
    (repo / ".env.dev").write_text("STORAGE_TYPE=local\n", encoding="utf-8")
    objects_manifest = module.OBJECT_BACKUP.create_backup(repo, root)
    return database_manifest, objects_manifest, root, archive


def test_verification_reports_both_stores_without_claiming_consistency(tmp_path):
    database, objects, root, _ = backup_fixture(tmp_path)
    report = module.assess_recovery_set(database, objects, backup_root=root)
    assert report["database_verified"] is True
    assert report["objects_verified"] is True
    assert report["database_restore_performed"] is False
    assert report["objects_restore_performed"] is False
    assert report["object_file_count"] == 1
    assert report["point_in_time_consistency"] == "unverified"
    assert isinstance(report["backup_time_gap_seconds"], int)


def test_rehearsal_restores_both_and_cleans_object_copy(tmp_path, monkeypatch):
    database, objects, root, _ = backup_fixture(tmp_path)
    restore_root = tmp_path / "isolated-objects"
    calls = []

    def fake_database_restore(backup, compose):
        calls.append((backup.sha256, compose))
        return {"restore_performed": True, "restored_row_counts": {"api_staff": 2}}

    monkeypatch.setattr(module.DATABASE_RESTORE, "restore_rehearsal", fake_database_restore)
    compose = object()
    report = module.assess_recovery_set(
        database, objects, backup_root=root, rehearse=True,
        restore_root=restore_root, compose=compose,
    )
    assert calls == [(report["database_sha256"], compose)]
    assert report["database_restore_performed"] is True
    assert report["objects_restore_performed"] is True
    assert report["restored_row_counts"] == {"api_staff": 2}
    assert report["restored_object_files"] == 1
    assert not list(restore_root.iterdir())


def test_tampered_database_stops_before_any_restore(tmp_path, monkeypatch):
    database, objects, root, archive = backup_fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"tamper")
    calls = []
    monkeypatch.setattr(
        module.DATABASE_RESTORE, "restore_rehearsal",
        lambda *_: calls.append("database"),
    )
    monkeypatch.setattr(
        module.OBJECT_BACKUP, "rehearse_restore",
        lambda *_: calls.append("objects"),
    )
    with pytest.raises(module.DATABASE_RESTORE.RehearsalError):
        module.assess_recovery_set(
            database, objects, backup_root=root, rehearse=True,
            restore_root=tmp_path / "isolated-objects", compose=object(),
        )
    assert calls == []


def test_tampered_object_stops_before_database_restore(tmp_path, monkeypatch):
    database, objects, root, _ = backup_fixture(tmp_path)
    stored = (
        objects.parent
        / "objects"
        / module.OBJECT_BACKUP.OBJECT_ROOTS[0]
        / "photo.bin"
    )
    stored.write_bytes(b"changed customer media")
    calls = []
    monkeypatch.setattr(
        module.DATABASE_RESTORE, "restore_rehearsal",
        lambda *_: calls.append("database"),
    )
    with pytest.raises(module.OBJECT_BACKUP.BackupError):
        module.assess_recovery_set(
            database, objects, backup_root=root, rehearse=True,
            compose=object(),
        )
    assert calls == []

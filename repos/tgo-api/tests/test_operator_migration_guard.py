"""A missing, partial or altered backup must stop operator deployment."""

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services.operator_migration import validate_backup


@pytest.fixture
def backup(tmp_path):
    content = b"PGDMP" + b"synthetic-test-archive" * 20
    (tmp_path / "database.dump").write_bytes(content)
    manifest = {
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "backupPath": str(tmp_path / "database.dump"),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "archiveListingVerified": True,
        "archiveListingLines": 20,
        "migrationApplied": False,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8-sig")
    return path, manifest


def test_valid_recent_backup_passes_integrity_check(backup):
    path, _ = backup
    assert validate_backup(path) == path.parent / "database.dump"


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "corrupt",
        "size",
        "unlisted",
        "empty_listing",
        "old",
        "future",
        "naive_time",
        "outside",
        "already_applied",
        "magic",
    ],
)
def test_invalid_backup_is_rejected(backup, case):
    path, manifest = backup
    archive = path.parent / "database.dump"
    if case == "missing":
        archive.unlink()
    elif case == "corrupt":
        archive.write_bytes(b"PGDMP" + b"altered archive")
    elif case == "size":
        manifest["bytes"] += 1
    elif case == "unlisted":
        manifest["archiveListingVerified"] = False
    elif case == "empty_listing":
        manifest["archiveListingLines"] = 0
    elif case in {"old", "future", "naive_time"}:
        now = datetime.now(timezone.utc)
        moment = {
            "old": now - timedelta(days=2),
            "future": now + timedelta(hours=1),
            "naive_time": now.replace(tzinfo=None),
        }[case]
        manifest["createdAtUtc"] = moment.isoformat()
    elif case == "outside":
        manifest["backupPath"] = str(path.parent.parent / "database.dump")
    elif case == "already_applied":
        manifest["migrationApplied"] = True
    elif case == "magic":
        content = b"wrong" + archive.read_bytes()[5:]
        archive.write_bytes(content)
        manifest["sha256"] = hashlib.sha256(content).hexdigest()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        validate_backup(path)


def test_invalid_manifest_does_not_echo_sensitive_content(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text('{"password": "secret"', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        validate_backup(path)
    assert "secret" not in str(error.value)

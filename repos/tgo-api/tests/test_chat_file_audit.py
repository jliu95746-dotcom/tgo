"""Historical attachment checks must be bounded, read-only and secret-free."""
# flake8: noqa: F811

import json
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.models import Project
from app.services.chat_file_audit import audit_chat_files
from tests.test_conversation_tenant_boundary import channel_app  # noqa: F401
from tests.test_private_chat_files import private_files  # noqa: F401


def audit(fixture, root, **kwargs):
    return audit_chat_files(
        fixture[1],
        storage_type="local",
        upload_root=root,
        **kwargs,
    )


def test_valid_files_are_checked_without_writes(private_files, tmp_path):
    db = private_files[1]
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        report = audit(private_files, tmp_path)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert report.total_records == report.active_records == 2
    assert report.local_files_checked == 2
    assert report.flagged_records == report.deleted_records == 0
    assert report.will_change_data is False
    assert report.issue_counts == {}
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


@pytest.mark.parametrize(
    "case,expected",
    [
        ("outside", "unsafe_storage_path"),
        ("absolute", "unsafe_storage_path"),
        ("windows_absolute", "unsafe_storage_path"),
        ("foreign_path", "path_project_mismatch"),
        ("legacy_path", "unrecognized_storage_layout"),
        ("missing", "local_file_missing"),
        ("size", "local_size_mismatch"),
        ("staff", "uploader_project_mismatch"),
        ("platform", "uploader_project_mismatch"),
        ("missing_uploader", "uploader_missing"),
        ("no_uploader", "uploader_unspecified"),
        ("two_uploaders", "multiple_uploaders"),
        ("missing_project", "project_missing"),
        ("deleted_project", "project_deleted"),
    ],
)
def test_detects_legacy_issues(private_files, tmp_path, case, expected):
    _, db, staff, platforms, files, _ = private_files
    file = files[0]
    if case == "outside":
        file.file_path = "../secret.txt"
    elif case == "absolute":
        file.file_path = str(tmp_path / "secret.txt")
    elif case == "windows_absolute":
        file.file_path = "C:\\secret.txt"
    elif case == "foreign_path":
        file.file_path = files[1].file_path
    elif case == "legacy_path":
        file.file_path = "legacy/sample.txt"
    elif case == "missing":
        (tmp_path / file.file_path).unlink()
    elif case == "size":
        file.file_size = 99
    elif case == "staff":
        file.uploaded_by_staff_id = staff[1].id
    elif case == "platform":
        file.uploaded_by_staff_id = None
        file.uploaded_by_platform_id = platforms[1].id
    elif case == "missing_uploader":
        file.uploaded_by_staff_id = uuid4()
    elif case == "no_uploader":
        file.uploaded_by_staff_id = None
    elif case == "two_uploaders":
        file.uploaded_by_platform_id = platforms[0].id
    elif case == "missing_project":
        file.project_id = uuid4()
    elif case == "deleted_project":
        db.get(Project, file.project_id).deleted_at = datetime.now(
            timezone.utc
        )
    db.commit()
    report = audit(private_files, tmp_path)
    assert report.issue_counts[expected] == 1
    assert report.flagged_records == 1
    serialized = json.dumps(asdict(report), default=str)
    for secret in ("secret.txt", "sample.txt", "key-0", "unused"):
        assert secret not in serialized


def test_deleted_records_are_counted_without_disk_checks(
    private_files, tmp_path
):
    file = private_files[4][0]
    file.deleted_at = datetime.now(timezone.utc)
    file.file_path = "../secret.txt"
    private_files[1].commit()
    report = audit(private_files, tmp_path)
    assert report.total_records == 2
    assert report.deleted_records == report.active_records == 1
    assert report.flagged_records == 0


def test_sample_limit_does_not_truncate_counts(private_files, tmp_path):
    for file in private_files[4]:
        file.file_size = 99
    private_files[1].commit()
    report = audit(private_files, tmp_path, sample_limit=1)
    assert report.flagged_records == 2
    assert report.issue_counts["local_size_mismatch"] == 2
    assert len(report.samples) == 1
    assert report.samples_truncated is True
    assert len(audit(private_files, tmp_path, sample_limit=0).samples) == 0


def test_cloud_does_not_claim_objects_or_acl_verified(private_files, tmp_path):
    report = audit_chat_files(
        private_files[1],
        storage_type="oss",
        upload_root=tmp_path,
    )
    assert report.local_files_checked == 0
    assert report.cloud_files_unverified == 2
    assert report.cloud_acl_status == "not_checked"
    assert report.flagged_records == 0


@pytest.mark.parametrize("limit", [-1, 101])
def test_invalid_sample_limit_is_rejected(private_files, tmp_path, limit):
    with pytest.raises(ValueError):
        audit(private_files, tmp_path, sample_limit=limit)


def test_audit_does_not_flush_pending_changes(private_files, tmp_path):
    db = private_files[1]
    file = private_files[4][0]
    file.file_size = 99
    db.autoflush = True
    audit(private_files, tmp_path)
    assert file in db.dirty
    db.rollback()
    assert file.file_size == 18


def test_disk_failure_is_reported_without_exposing_path(
    private_files,
    tmp_path,
    monkeypatch,
):
    from pathlib import Path

    def denied(self):
        raise PermissionError("secret storage path")

    monkeypatch.setattr(Path, "is_file", denied)
    report = audit(private_files, tmp_path)
    assert report.issue_counts["local_file_unreadable"] == 2
    assert "secret storage path" not in json.dumps(asdict(report), default=str)

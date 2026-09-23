"""Read-only inventory of historical attachment metadata and local storage."""

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import ChatFile, Platform, Project, Staff


@dataclass(frozen=True)
class AttachmentAuditSample:
    file_id: UUID
    project_id: UUID
    issues: tuple[str, ...]


@dataclass
class AttachmentAuditReport:
    storage_type: str
    will_change_data: bool = False
    cloud_acl_status: str = "not_checked"
    total_records: int = 0
    active_records: int = 0
    deleted_records: int = 0
    flagged_records: int = 0
    local_files_checked: int = 0
    cloud_files_unverified: int = 0
    issue_counts: dict[str, int] = field(default_factory=dict)
    samples: list[AttachmentAuditSample] = field(default_factory=list)
    samples_truncated: bool = False


def _check_path(file: ChatFile) -> list[str]:
    value = file.file_path
    if (
        not value
        or "\\" in value
        or any(ord(character) < 32 for character in value)
        or PureWindowsPath(value).drive
        or PurePosixPath(value).is_absolute()
        or ".." in value.split("/")
    ):
        return ["unsafe_storage_path"]
    parts = PurePosixPath(value).parts
    if len(parts) < 3 or parts[0] != "chat":
        return ["unrecognized_storage_layout"]
    try:
        path_project = UUID(parts[1])
    except ValueError:
        return ["unrecognized_storage_layout"]
    if path_project != file.project_id:
        return ["path_project_mismatch"]
    return []


def _check_local_file(file: ChatFile, root: Path) -> list[str]:
    try:
        target = (root / file.file_path).resolve()
        if not target.is_relative_to(root):
            return ["unsafe_storage_path"]
        if not target.is_file():
            return ["local_file_missing"]
        if target.stat().st_size != file.file_size:
            return ["local_size_mismatch"]
    except (OSError, RuntimeError, ValueError):
        # Do not include paths or exception strings in operator reports.
        return ["local_file_unreadable"]
    return []


def audit_chat_files(
    db: Session,
    *,
    storage_type: str,
    upload_root: Path,
    sample_limit: int = 20,
) -> AttachmentAuditReport:
    """Scan metadata in batches without reading content or contacting clouds.

    Channel access, old message URLs and bucket ACLs require separate checks.
    Deleted uploaders remain valid historical owners when tenant IDs match.
    The command caller enforces a PostgreSQL read-only snapshot transaction.
    """
    if not 0 <= sample_limit <= 100:
        raise ValueError("sample_limit must be between 0 and 100")
    if storage_type not in ("local", "oss", "minio"):
        raise ValueError("unsupported storage_type")
    root = upload_root.resolve() if storage_type == "local" else upload_root
    report = AttachmentAuditReport(storage_type=storage_type)
    query = (
        db.query(
            ChatFile,
            Project.id,
            Project.deleted_at,
            Staff.project_id,
            Platform.project_id,
        )
        .outerjoin(Project, Project.id == ChatFile.project_id)
        .outerjoin(Staff, Staff.id == ChatFile.uploaded_by_staff_id)
        .outerjoin(Platform, Platform.id == ChatFile.uploaded_by_platform_id)
        .order_by(ChatFile.id)
        .yield_per(500)
    )
    with db.no_autoflush:
        for file, project_id, deleted_at, staff_owner, platform_owner in query:
            report.total_records += 1
            if file.deleted_at is not None:
                report.deleted_records += 1
                continue
            report.active_records += 1
            issues: list[str] = []
            if project_id is None:
                issues.append("project_missing")
            elif deleted_at is not None:
                issues.append("project_deleted")
            uploaders = (
                (file.uploaded_by_staff_id, staff_owner),
                (file.uploaded_by_platform_id, platform_owner),
            )
            count = sum(identifier is not None for identifier, _ in uploaders)
            if count == 0:
                issues.append("uploader_unspecified")
            elif count > 1:
                issues.append("multiple_uploaders")
            for identifier, owner in uploaders:
                if identifier is not None:
                    if owner is None:
                        issues.append("uploader_missing")
                    elif owner != file.project_id:
                        issues.append("uploader_project_mismatch")
            path_issues = _check_path(file)
            issues.extend(path_issues)
            if storage_type == "local":
                if "unsafe_storage_path" not in path_issues:
                    report.local_files_checked += 1
                    issues.extend(_check_local_file(file, root))
            else:
                report.cloud_files_unverified += 1
            unique_issues = tuple(sorted(set(issues)))
            if unique_issues:
                report.flagged_records += 1
                for issue in unique_issues:
                    report.issue_counts[issue] = (
                        report.issue_counts.get(issue, 0) + 1
                    )
                if len(report.samples) < sample_limit:
                    report.samples.append(
                        AttachmentAuditSample(
                            file_id=file.id,
                            project_id=file.project_id,
                            issues=unique_issues,
                        )
                    )
    report.samples_truncated = report.flagged_records > len(report.samples)
    return report

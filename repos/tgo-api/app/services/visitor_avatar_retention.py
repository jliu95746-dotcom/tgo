"""Clean generated visitor avatar files left by interrupted uploads."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Visitor


_GENERATED_IMAGE = re.compile(
    r"[0-9]{13}_[0-9a-f]{16}\.(?:jpg|jpeg|png|gif|webp)\Z"
)


@dataclass(frozen=True)
class AvatarCleanupResult:
    deleted: int = 0
    errors: int = 0


def _canonical_uuid(name: str) -> UUID | None:
    try:
        value = UUID(name)
    except ValueError:
        return None
    return value if str(value) == name else None


def _candidate_paths(root: Path) -> Iterator[tuple[UUID, UUID, Path]]:
    avatars = root / "avatars"
    if not avatars.is_dir() or avatars.is_symlink():
        return
    for project_directory in avatars.iterdir():
        if (
            project_directory.is_symlink()
            or not project_directory.is_dir()
        ):
            continue
        project_id = _canonical_uuid(project_directory.name)
        if project_id is None:
            continue
        for visitor_directory in project_directory.iterdir():
            if (
                visitor_directory.is_symlink()
                or not visitor_directory.is_dir()
            ):
                continue
            visitor_id = _canonical_uuid(visitor_directory.name)
            if visitor_id is None:
                continue
            for candidate in visitor_directory.iterdir():
                if _GENERATED_IMAGE.fullmatch(candidate.name):
                    yield project_id, visitor_id, candidate


def _current_or_unknown(
    db: Session, project_id: UUID, visitor_id: UUID, filename: str,
) -> bool:
    # The row lock coordinates deletion with avatar commits on shared storage.
    record = db.execute(
        select(Visitor.project_id, Visitor.avatar_url)
        .where(Visitor.id == visitor_id).with_for_update()
    ).one_or_none()
    if record is None:
        return False
    if record.project_id != project_id:
        return True
    if not record.avatar_url:
        return False
    url = str(record.avatar_url)
    parsed = urlsplit(url)
    if (
        parsed.scheme or parsed.netloc or parsed.fragment
        or parsed.path != f"/v1/visitors/{visitor_id}/avatar"
    ):
        return True
    if not parsed.query:
        # An older URL has no filename, so its owner cannot be identified.
        return True
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    versions = parameters.get("v", [])
    if set(parameters) != {"v"} or len(versions) != 1:
        return True
    return versions[0] == filename


def cleanup_orphan_visitor_avatars(
    db: Session, *, now: float | None = None,
    older_than_seconds: int = 7 * 24 * 60 * 60, limit: int = 100,
) -> AvatarCleanupResult:
    """Delete old unbound generated files; never guess legacy ownership."""
    if older_than_seconds < 24 * 60 * 60 or limit < 1:
        raise ValueError("Unsafe avatar cleanup bounds")
    current_time = time.time() if now is None else now
    root = Path(settings.UPLOAD_BASE_DIR).resolve()
    deleted = errors = 0
    for project_id, visitor_id, candidate in _candidate_paths(root):
        if deleted >= limit:
            break
        try:
            file_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            continue
        if file_stat.st_mtime > current_time - older_than_seconds:
            continue
        try:
            if _current_or_unknown(db, project_id, visitor_id, candidate.name):
                continue
            # Re-stat under the DB lock so a replaced or active file survives.
            current_stat = candidate.lstat()
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or (
                    current_stat.st_dev, current_stat.st_ino,
                    current_stat.st_mtime_ns, current_stat.st_size,
                ) != (
                    file_stat.st_dev, file_stat.st_ino,
                    file_stat.st_mtime_ns, file_stat.st_size,
                )
            ):
                continue
            candidate.unlink()
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
        finally:
            db.rollback()
    return AvatarCleanupResult(deleted=deleted, errors=errors)

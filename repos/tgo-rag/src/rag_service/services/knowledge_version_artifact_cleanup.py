"""Recover website artifacts while fencing their source's final transaction."""

from pathlib import Path
from typing import Literal, TypedDict
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import File
from ..models.knowledge_versions import (
    KnowledgeVersion,
    KnowledgeVersionSource,
)
from .knowledge_version_artifacts import (
    BODY,
    NAMESPACE,
    RECEIPT,
    ArtifactReceipt,
    directory_key,
    read_receipt,
    remove_owned_files,
    save_cursor,
    scan_artifacts,
)

CleanupOutcome = Literal["removed", "retained", "busy", "invalid"]
# Serialize only this housekeeping job, without locking unrelated work.
CLEANUP_LOCK = 7_104_307_381_106_468


class ArtifactCleanupResult(TypedDict):
    scanned: int
    removed: int
    retained: int
    busy: int
    invalid: int
    failed: int


async def cleanup_artifact(
    db: AsyncSession,
    directory: Path,
    receipt: ArtifactReceipt | None = None,
) -> CleanupOutcome:
    key = directory_key(directory)
    if key is None:
        return "invalid"
    if not db.in_transaction():
        await db.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
    if (
        await db.scalar(select(func.current_setting("transaction_isolation")))
        != "read committed"
    ):
        raise ValueError("不能确认文件引用的最新状态，已保留文件。")
    source_id, artifact_id = key
    source = (
        await db.scalars(
            select(KnowledgeVersionSource)
            .where(KnowledgeVersionSource.id == UUID(source_id))
            .with_for_update(skip_locked=True)
        )
    ).one_or_none()
    if source is None:
        present = await db.scalar(
            select(
                exists().where(KnowledgeVersionSource.id == UUID(source_id))
            )
        )
        if not isinstance(present, bool):
            raise ValueError("不能确认文件来源的最新状态，已保留文件。")
        if present:
            return "busy"
    if not directory.exists():
        return "removed"
    try:
        if receipt is None:
            # A process can die after mkdir but before its receipt write.
            if not any(directory.iterdir()):
                remove_owned_files(directory)
                return "removed"
            if (
                not (directory / BODY).exists()
                and not (directory / RECEIPT).exists()
            ):
                return "invalid"
            receipt = read_receipt(directory)
        if (source_id, artifact_id) != (
            str(receipt.source),
            str(receipt.artifact),
        ) or (
            source is not None
            and (
                source.project_id != receipt.project
                or source.source_kind != "website"
            )
        ):
            return "invalid"
    except ValueError:
        if not (directory / BODY).exists():
            try:
                remove_owned_files(directory)
                return "removed"
            except ValueError:
                pass
        return "invalid"
    # Suffixes also protect references persisted under an earlier upload mount.
    suffix = f"/{NAMESPACE}/{source_id}/{artifact_id}/{BODY}"
    alternate = suffix.replace("/", "\\")
    paths = [receipt.reference_path.lower(), str(directory / BODY).lower()]
    file_path = func.lower(File.storage_path)
    snapshot_path = func.lower(
        KnowledgeVersion.snapshot["storage_path"].as_string()
    )
    referenced = await db.scalar(
        select(
            or_(
                exists().where(
                    or_(
                        file_path.in_(paths),
                        file_path.endswith(suffix, autoescape=True),
                        file_path.endswith(alternate, autoescape=True),
                    )
                ),
                exists().where(
                    or_(
                        snapshot_path.in_(paths),
                        snapshot_path.endswith(suffix, autoescape=True),
                        snapshot_path.endswith(alternate, autoescape=True),
                    )
                ),
                # Retained versions are an ownership reference even if a
                # damaged snapshot no longer contains its storage path.
                exists().where(
                    KnowledgeVersion.id == receipt.version,
                    KnowledgeVersion.source_id == receipt.source,
                    KnowledgeVersion.state.in_(
                        ["ready", "pending_review", "published", "retired"]
                    ),
                ),
            )
        )
    )
    if not isinstance(referenced, bool):
        raise ValueError("不能确认文件引用，已保留文件。")
    if referenced:
        return "retained"
    try:
        remove_owned_files(directory)
    except ValueError:
        return "invalid"
    return "removed"


async def collect_artifacts(
    db: AsyncSession, limit: int
) -> ArtifactCleanupResult:
    result: ArtifactCleanupResult = {
        "scanned": 0,
        "removed": 0,
        "retained": 0,
        "busy": 0,
        "invalid": 0,
        "failed": 0,
    }
    if not db.in_transaction():
        await db.connection(
            execution_options={"isolation_level": "READ COMMITTED"}
        )
    if not await db.scalar(
        select(func.pg_try_advisory_xact_lock(CLEANUP_LOCK))
    ):
        result["busy"] = 1
        return result
    candidates = scan_artifacts(limit)
    for directory in candidates:
        result["scanned"] += 1
        try:
            outcome = await cleanup_artifact(db, directory)
            result[outcome] += 1
        except OSError:
            result["failed"] += 1
    if candidates:
        last = candidates[-1]
        save_cursor((last.parent.name, last.name))
    else:
        save_cursor(None)
    return result

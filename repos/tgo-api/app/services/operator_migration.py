"""Check backup integrity and deploy only the operator revision."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StrictBool,
    ValidationError,
)
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

BASE_REVISION = "0036_retire_personal_wechat"
TARGET_REVISION = "0037_platform_operators"


class BackupManifest(BaseModel):
    createdAtUtc: AwareDatetime
    backupPath: Path
    bytes: int = Field(gt=5)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    archiveListingVerified: StrictBool
    archiveListingLines: int = Field(gt=0)
    migrationApplied: StrictBool


def validate_backup(manifest_path: Path) -> Path:
    """Check a recent archive; this does not prove a successful restore."""
    manifest_path = manifest_path.resolve(strict=True)
    if manifest_path.stat().st_size > 65536:
        raise ValueError("Backup manifest is too large")
    try:
        manifest = BackupManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8-sig")
        )
    except (ValidationError, UnicodeError) as exc:
        raise ValueError("Backup manifest is invalid") from exc
    archive = (manifest_path.parent / "database.dump").resolve(strict=True)
    if archive.parent != manifest_path.parent:
        raise ValueError("Backup archive must remain beside its manifest")
    if manifest.backupPath.resolve(strict=True) != archive:
        raise ValueError("Backup archive path does not match its manifest")
    age = datetime.now(timezone.utc) - manifest.createdAtUtc
    if age < -timedelta(minutes=5) or age > timedelta(hours=24):
        raise ValueError("A backup from the last 24 hours is required")
    if not manifest.archiveListingVerified or manifest.migrationApplied:
        raise ValueError("A verified pre-migration archive is required")
    if archive.stat().st_size != manifest.bytes:
        raise ValueError("Backup archive size mismatch")
    digest = sha256()
    with archive.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Backup is not a PostgreSQL custom archive")
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != manifest.sha256.lower():
        raise ValueError("Backup archive checksum mismatch")
    return archive


def apply_operator_revision(connection: Connection) -> bool:
    """Apply only 0037 in the caller's transaction; never commit here."""
    if connection.dialect.name != "postgresql":
        raise ValueError("Operator deployment requires PostgreSQL")
    if not connection.in_transaction():
        raise ValueError("A caller-owned transaction is required")
    connection.execute(text("SET LOCAL lock_timeout = '5s'"))
    connection.execute(text("SET LOCAL statement_timeout = '30s'"))
    # Serialize deployments; do not race two copies of CREATE TABLE.
    connection.execute(text("SELECT pg_advisory_xact_lock(873416, 37)"))
    inspector = inspect(connection)
    if not inspector.has_table("api_alembic_version"):
        raise ValueError("API migration version table is missing")
    versions = (
        connection.execute(text("SELECT version_num FROM api_alembic_version"))
        .scalars()
        .all()
    )
    has_operators = inspector.has_table("api_platform_operators")
    if versions == [TARGET_REVISION] and has_operators:
        columns = {
            column["name"]
            for column in inspector.get_columns("api_platform_operators")
        }
        required = {
            "id", "email", "name", "password_hash", "is_active",
            "token_version", "created_at", "last_login_at", "deleted_at",
        }
        if not required.issubset(columns):
            raise ValueError("Existing operator table is incomplete")
        return False
    if versions != [BASE_REVISION] or has_operators:
        raise ValueError("Unexpected API revision or operator table state")
    generic_versions = None
    if inspector.has_table("alembic_version"):
        generic_versions = (
            connection.execute(
                text(
                    "SELECT version_num FROM alembic_version "
                    "ORDER BY version_num"
                )
            )
            .scalars()
            .all()
        )
    root = Path(__file__).resolve().parents[2]
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["connection"] = connection
    command.upgrade(config, TARGET_REVISION)
    if connection.execute(
        text("SELECT version_num FROM api_alembic_version")
    ).scalars().all() != [TARGET_REVISION]:
        raise RuntimeError("Operator revision was not applied")
    if generic_versions is not None:
        after = (
            connection.execute(
                text(
                    "SELECT version_num FROM alembic_version "
                    "ORDER BY version_num"
                )
            )
            .scalars()
            .all()
        )
        if after != generic_versions:
            raise RuntimeError("Another service's migration version changed")
    return True

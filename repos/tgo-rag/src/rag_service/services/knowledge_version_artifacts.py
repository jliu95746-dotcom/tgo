"""Private website blobs with a durable receipt written before their body."""

from dataclasses import dataclass
import heapq
import os
from pathlib import Path
from typing import Iterator, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..models.knowledge_versions import KnowledgeVersionSource

NAMESPACE = ".knowledge-version-artifacts"
BODY = "content.md"
RECEIPT = "manifest.json"
CURSOR = ".cleanup-cursor.json"
ArtifactKey = tuple[str, str]


class ArtifactReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal[1] = 1
    project: UUID
    source: UUID
    version: UUID
    attempt: UUID
    artifact: UUID
    reference_path: str = Field(min_length=1, max_length=4096)


def artifact_root() -> Path:
    return Path(get_settings().upload_dir).resolve() / NAMESPACE


def plain(path: Path) -> bool:
    """Reject symlinks, junctions and aliases before reading or deleting."""
    return not path.is_symlink() and path.resolve() == path


def uuid_name(name: str) -> bool:
    try:
        return str(UUID(name)) == name
    except ValueError:
        return False


def directory_key(directory: Path) -> ArtifactKey | None:
    root = artifact_root()
    if (
        directory.parent.parent != root
        or not uuid_name(directory.parent.name)
        or not uuid_name(directory.name)
        or not all(plain(path) for path in (root, directory.parent, directory))
    ):
        return None
    return directory.parent.name, directory.name


def write_new(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass
class WebsiteArtifact:
    receipt: ArtifactReceipt
    directory: Path
    created: bool = False

    @property
    def path(self) -> Path:
        return self.directory / BODY

    def write(self, markdown: str) -> None:
        """Celery caller must hold the existing source's database lock."""
        root = artifact_root()
        if directory_key(self.directory) != (
            str(self.receipt.source),
            str(self.receipt.artifact),
        ) or self.receipt.reference_path != str(self.path):
            raise ValueError("知识版本文件归属不一致。")
        root.mkdir(mode=0o700, exist_ok=True)
        if not plain(root):
            raise ValueError("知识版本文件目录不可用。")
        self.directory.parent.mkdir(mode=0o700, exist_ok=True)
        if directory_key(self.directory) != (
            str(self.receipt.source),
            str(self.receipt.artifact),
        ):
            raise ValueError("知识版本文件归属不一致。")
        self.directory.mkdir(mode=0o700)
        self.created = True
        write_new(
            self.directory / RECEIPT,
            self.receipt.model_dump_json().encode("utf-8"),
        )
        write_new(self.path, markdown.encode("utf-8"))


def website_artifact(
    source: KnowledgeVersionSource, version: UUID, attempt: UUID
) -> WebsiteArtifact:
    if source.source_kind != "website":
        raise ValueError("只有网站版本可以创建网站正文文件。")
    identifier = uuid4()
    directory = artifact_root() / str(source.id) / str(identifier)
    return WebsiteArtifact(
        ArtifactReceipt(
            project=source.project_id,
            source=source.id,
            version=version,
            attempt=attempt,
            artifact=identifier,
            reference_path=str(directory / BODY),
        ),
        directory,
    )


def read_receipt(directory: Path) -> ArtifactReceipt:
    path = directory / RECEIPT
    if not plain(path) or not path.is_file():
        raise ValueError("知识版本文件清理记录不可用。")
    with path.open("rb") as stream:
        data = stream.read(8193)
    if len(data) > 8192:
        raise ValueError("知识版本文件清理记录无效。")
    receipt = ArtifactReceipt.model_validate_json(data)
    if directory_key(directory) != (
        str(receipt.source),
        str(receipt.artifact),
    ):
        raise ValueError("知识版本文件清理记录归属不一致。")
    suffix = f"/{NAMESPACE}/{receipt.source}/{receipt.artifact}/{BODY}"
    if not receipt.reference_path.replace("\\", "/").endswith(suffix):
        raise ValueError("知识版本文件清理路径无效。")
    return receipt


def remove_owned_files(directory: Path) -> None:
    if directory_key(directory) is None:
        raise ValueError("不能清理未知知识文件目录。")
    if not directory.exists():
        return
    children = list(directory.iterdir())
    if any(
        path.name not in {BODY, RECEIPT}
        or not plain(path)
        or not path.is_file()
        for path in children
    ):
        raise ValueError("知识版本目录包含未知文件。")
    for name in (BODY, RECEIPT):
        (directory / name).unlink(missing_ok=True)
    directory.rmdir()
    # Another artifact can share this source directory; never recurse.
    if not any(directory.parent.iterdir()):
        directory.parent.rmdir()


def candidate_keys() -> Iterator[ArtifactKey]:
    root = artifact_root()
    if not root.exists():
        return
    if not plain(root):
        raise ValueError("知识版本文件目录不可用。")
    for source in root.iterdir():
        if (
            not uuid_name(source.name)
            or not source.is_dir()
            or not plain(source)
        ):
            continue
        for directory in source.iterdir():
            if directory.is_dir() and directory_key(directory) is not None:
                yield source.name, directory.name


class CleanupCursor(BaseModel):
    key: ArtifactKey | None = None


def read_cursor() -> ArtifactKey | None:
    path = artifact_root() / CURSOR
    if not path.exists():
        return None
    if not plain(path):
        raise ValueError("知识版本清理游标不可用。")
    try:
        with path.open("rb") as stream:
            value = CleanupCursor.model_validate_json(stream.read(1025))
        if value.key and all(uuid_name(part) for part in value.key):
            return value.key
    except ValueError:
        pass
    return None


def scan_artifacts(limit: int) -> list[Path]:
    if not 1 <= limit <= 1000:
        raise ValueError("知识版本文件清理批量必须在 1 到 1000 之间。")
    cursor = read_cursor()
    keys = heapq.nsmallest(
        limit,
        (key for key in candidate_keys() if cursor is None or key > cursor),
    )
    if not keys and cursor is not None:
        keys = heapq.nsmallest(limit, candidate_keys())
    root = artifact_root()
    return [root / source / artifact for source, artifact in keys]


def save_cursor(key: ArtifactKey | None) -> None:
    """Caller holds the collector's transaction advisory lock."""
    root = artifact_root()
    if not root.exists():
        return
    destination = root / CURSOR
    if not plain(root) or not plain(destination):
        raise ValueError("知识版本清理游标不可用。")
    for abandoned in root.glob(".cleanup-cursor.*.tmp"):
        token = abandoned.name.removeprefix(".cleanup-cursor.").removesuffix(
            ".tmp"
        )
        if uuid_name(token) and plain(abandoned) and abandoned.is_file():
            abandoned.unlink()
    temporary = root / f".cleanup-cursor.{uuid4()}.tmp"
    try:
        write_new(temporary, CleanupCursor(key=key).model_dump_json().encode())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

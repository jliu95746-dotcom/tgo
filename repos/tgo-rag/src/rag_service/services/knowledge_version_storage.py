"""Delete only confirmed, unreferenced history blobs inside the configured upload directory."""
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import File
from ..models.knowledge_versions import KnowledgeVersion, KnowledgeVersionSource


async def detach_unused_blobs(db: AsyncSession, source: KnowledgeVersionSource,
                              candidates: set[str]) -> list[Path]:
    """Caller holds source lock; physical unlink happens only after successful commit."""
    await db.flush()
    references = (await db.execute(select(
        KnowledgeVersion.snapshot['storage_path'].astext,
        KnowledgeVersion.snapshot['content']['replacement_file_id'].astext,
    ))).all()
    referenced_paths = {path for path, _ in references if path}
    referenced_ids = {identifier for _, identifier in references if identifier}
    root = Path(get_settings().upload_dir).resolve()
    removable: list[Path] = []
    for candidate in candidates - referenced_paths:
        path = Path(candidate).resolve()
        if path == root or not path.is_relative_to(root):
            continue
        files = (await db.execute(select(File).where(File.storage_path == candidate).with_for_update())).scalars().all()
        if any(file.collection_id is not None or file.project_id != source.project_id
               or (file.storage_metadata or {}).get('version_source') != str(source.id)
               or str(file.id) in referenced_ids for file in files):
            continue
        for file in files:
            await db.delete(file)
        removable.append(path)
    return removable


def remove_blobs(paths: list[Path]) -> int:
    """A filesystem error never invalidates the already-published current version."""
    failed = 0
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failed += 1
    return failed

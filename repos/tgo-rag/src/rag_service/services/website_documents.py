"""Atomically replace a crawled page's searchable text and pgvector rows."""

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from uuid import UUID

from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db_session
from ..logging_config import get_logger
from ..models import Collection, File, FileDocument, WebsitePage
from .embedding import get_embedding_service_for_project

logger = get_logger(__name__)


class WebsiteChunk(TypedDict):
    id: UUID
    content: str
    character_count: int
    token_count: int
    chunk_index: int
    document_type: str
    metadata: dict[str, str | int | float | bool]


async def current_source(
    db: AsyncSession,
    source: File,
) -> tuple[WebsitePage, File] | None:
    """Lock the current source and its live tenant-owned collection together."""
    result = await db.execute(
        select(WebsitePage, File)
        .join(File, File.id == WebsitePage.file_id)
        .join(Collection, Collection.id == WebsitePage.collection_id)
        .where(
            File.id == source.id,
            File.project_id == source.project_id,
            File.collection_id == source.collection_id,
            File.deleted_at.is_(None),
            File.storage_metadata["source"].astext == "website_crawl",
            WebsitePage.project_id == source.project_id,
            WebsitePage.collection_id == source.collection_id,
            Collection.project_id == source.project_id,
            Collection.deleted_at.is_(None),
        )
        .with_for_update(of=(Collection, WebsitePage, File))
    )
    row = result.one_or_none()
    if row is None:
        return None
    page, file = row
    if (file.storage_metadata or {}).get("page_id") != str(page.id):
        return None
    return page, file


async def claim_website_file(source: File) -> bool:
    """Duplicate deliveries must not reset an in-flight or completed file."""
    async with get_db_session() as db:
        current = await current_source(db, source)
        if current is None:
            return False
        page, file = current
        if page.status != "processing" or file.status != "pending":
            return False
        file.status = "processing"
        file.error_message = None
        await db.commit()
        return True


async def publish_website_documents(
    source: File,
    chunks: list[WebsiteChunk],
) -> bool:
    """No searchable rows are inserted until every embedding is validated."""
    project_id = UUID(str(source.project_id))
    collection_id = UUID(str(source.collection_id))
    if not chunks or any(not chunk["content"].strip() for chunk in chunks):
        raise ValueError("Website chunks cannot be empty")
    service = await get_embedding_service_for_project(source.project_id)
    vectors = await service.generate_embeddings_batch(
        [chunk["content"] for chunk in chunks],
    )
    dimensions = getattr(FileDocument.__table__.c.embedding.type, "dim", None)
    if len(vectors) != len(chunks) or any(
        len(vector) != dimensions
        or not any(vector)
        or not all(math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise ValueError("Website embeddings have invalid count or values")
    model = service.get_embedding_model()

    async with get_db_session() as db:
        current = await current_source(db, source)
        if current is None:
            return False
        page, file = current
        if page.status != "processing" or file.status not in {
            "processing",
            "chunking_documents",
            "generating_embeddings",
        }:
            return False
        page_id = page.id
        retired_result = await db.execute(
            select(File)
            .where(
                File.project_id == source.project_id,
                File.collection_id == source.collection_id,
                File.id != source.id,
                File.storage_metadata["source"].astext == "website_crawl",
                File.storage_metadata["page_id"].astext == str(page_id),
            )
            .with_for_update()
        )
        retired = list(retired_result.scalars().all())
        # Vectors are columns on FileDocument, so this also removes the index.
        # Delete current rows too to repair partial writes from older workers.
        await db.execute(
            delete(FileDocument).where(
                FileDocument.project_id == source.project_id,
                FileDocument.collection_id == source.collection_id,
                FileDocument.file_id.in_([source.id] + [old.id for old in retired]),
            )
        )
        documents = [
            FileDocument(
                id=chunk["id"],
                project_id=source.project_id,
                collection_id=source.collection_id,
                file_id=source.id,
                content=chunk["content"],
                content_length=chunk["character_count"],
                token_count=chunk["token_count"],
                chunk_index=chunk["chunk_index"],
                content_type=chunk["document_type"],
                tags=chunk["metadata"],
                embedding=vector,
                embedding_dimensions=len(vector),
                embedding_model=model,
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        db.add_all(documents)
        await db.flush()
        await db.execute(
            update(FileDocument)
            .where(
                FileDocument.file_id == source.id,
                FileDocument.project_id == source.project_id,
            )
            .values(
                content_tsv=func.to_tsvector(
                    literal_column("'pg_catalog.english'::regconfig"),
                    FileDocument.content,
                )
            )
        )
        for old in retired:
            # Keep a durable cleanup record until physical deletion succeeds.
            old.status = "archived"
            old.deleted_at = datetime.now(timezone.utc)
            old.document_count = 0
            old.total_tokens = 0
        file.status = "completed"
        file.error_message = None
        file.document_count = len(chunks)
        file.total_tokens = sum(chunk["token_count"] for chunk in chunks)
        page.status = "processed"
        page.error_message = None
        await db.commit()

    # Never turn a successfully published version into 'failed' for cleanup IO.
    try:
        await cleanup_retired_files(project_id, collection_id, page_id)
    except Exception as error:
        logger.warning(
            "Website cleanup deferred",
            page_id=str(page_id),
            error_type=type(error).__name__,
        )
    return True


def owned_storage_path(upload_dir: str, file_id: UUID, storage_path: str) -> Path:
    root = Path(upload_dir).resolve()
    candidate = Path(storage_path).resolve()
    expected = root / str(file_id)
    if candidate != expected or candidate.parent != root:
        raise ValueError("Retired website file is outside its owned storage path")
    return candidate


async def cleanup_retired_files(
    project_id: UUID,
    collection_id: UUID,
    page_id: UUID,
) -> None:
    """Retryable cleanup of only archived, generated files of this exact page."""
    async with get_db_session() as db:
        result = await db.execute(
            select(File)
            .where(
                File.project_id == project_id,
                File.collection_id == collection_id,
                File.status == "archived",
                File.deleted_at.is_not(None),
                File.storage_provider == "local",
                File.storage_metadata["source"].astext == "website_crawl",
                File.storage_metadata["page_id"].astext == str(page_id),
            )
            .with_for_update()
        )
        for file in result.scalars().all():
            try:
                path = owned_storage_path(
                    get_settings().upload_dir,
                    file.id,
                    file.storage_path,
                )
                path.unlink(missing_ok=True)
            except (OSError, ValueError) as error:
                logger.warning(
                    "Retired website file cleanup deferred",
                    file_id=str(file.id),
                    error_type=type(error).__name__,
                )
                continue
            await db.execute(
                delete(File).where(
                    File.id == file.id,
                    File.project_id == project_id,
                    File.status == "archived",
                    File.deleted_at.is_not(None),
                )
            )
        await db.commit()

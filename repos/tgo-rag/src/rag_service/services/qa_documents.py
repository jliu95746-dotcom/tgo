"""Publish QA text and its pgvector embedding in one RAG-owned transaction."""

import math
from dataclasses import dataclass
from typing import TypedDict
from uuid import UUID, uuid4

from sqlalchemy import Select, func, literal_column, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db_session
from ..logging_config import get_logger
from ..models import Collection, FileDocument, QAPair
from .embedding import get_embedding_service_for_project
from .qa_errors import safe_qa_failure

logger = get_logger(__name__)


class QAProcessingResult(TypedDict, total=False):
    success: bool
    qa_pair_id: str
    document_id: str
    vector_id: str
    error: str
    skipped: bool


@dataclass(frozen=True)
class QASnapshot:
    question: str
    answer: str
    collection_id: UUID

    @classmethod
    def capture(cls, pair: QAPair) -> "QASnapshot":
        return cls(pair.question, pair.answer, pair.collection_id)

    @property
    def content(self) -> str:
        return build_qa_content(self.question, self.answer)


def build_qa_content(question: str, answer: str) -> str:
    return f"问题: {question}\n\n答案: {answer}"


def current_pair_query(pair_id: UUID, project_id: UUID) -> Select[tuple[QAPair]]:
    return (
        select(QAPair)
        .join(Collection, Collection.id == QAPair.collection_id)
        .where(
            QAPair.id == pair_id,
            QAPair.project_id == project_id,
            QAPair.deleted_at.is_(None),
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
        .with_for_update(of=QAPair)
    )


async def current_pair(
    db: AsyncSession, pair_id: UUID, project_id: UUID
) -> QAPair | None:
    result = await db.execute(current_pair_query(pair_id, project_id))
    pair = result.scalar_one_or_none()
    # Retain the invariant even for custom repository/session adapters.
    if pair and (pair.project_id != project_id or pair.deleted_at is not None):
        return None
    return pair


async def owned_document(db: AsyncSession, pair: QAPair) -> FileDocument | None:
    if pair.document_id is None:
        return None
    result = await db.execute(
        select(FileDocument).where(
            FileDocument.id == pair.document_id,
            FileDocument.project_id == pair.project_id,
            FileDocument.collection_id == pair.collection_id,
            FileDocument.file_id.is_(None),
        )
    )
    document = result.scalar_one_or_none()
    if document is not None and (document.tags or {}).get("qa_pair_id") != str(pair.id):
        raise ValueError("QA document ownership does not match")
    return document


async def remove_qa_document(db: AsyncSession, pair: QAPair) -> None:
    """The vector is a column on FileDocument, not a separate external index."""
    document = await owned_document(db, pair)
    pair.document_id = None
    await db.flush()
    if document is not None:
        await db.delete(document)


async def process_qa_pair_async(
    qa_pair_id: UUID, project_id: UUID, is_update: bool = False
) -> QAProcessingResult:
    # is_update stays wire-compatible; a persisted document is reused on every retry.
    snapshot: QASnapshot | None = None
    try:
        async with get_db_session() as db:
            pair = await current_pair(db, qa_pair_id, project_id)
            if pair is None:
                return {"success": False, "error": "QA pair not found for project"}
            snapshot = QASnapshot.capture(pair)
            pair.status = "processing"
            pair.error_message = None
            await db.commit()

        service = await get_embedding_service_for_project(project_id)
        vector = await service.generate_embedding(snapshot.content)
        dimensions = getattr(FileDocument.__table__.c.embedding.type, "dim", None)
        if (len(vector) != dimensions
                or not all(math.isfinite(value) for value in vector)):
            raise ValueError(
                "Embedding is empty, non-finite or has invalid dimensions"
            )
        if not any(vector):
            raise ValueError("Embedding is a zero vector")
        model = service.get_embedding_model()

        async with get_db_session() as db:
            pair = await current_pair(db, qa_pair_id, project_id)
            if pair is None or QASnapshot.capture(pair) != snapshot:
                return {"success": False, "skipped": True,
                        "error": "QA content changed or was deleted during processing"}
            document = await owned_document(db, pair)
            if document is None:
                document = FileDocument(
                    id=uuid4(), project_id=project_id,
                    collection_id=pair.collection_id, file_id=None,
                )
                db.add(document)
            document.content = snapshot.content
            document.document_title = pair.question[:500]
            document.content_length = len(snapshot.content)
            document.token_count = len(snapshot.content.split())
            document.chunk_index = 0
            document.content_type = "qa_pair"
            document.tags = {
                "qa_pair_id": str(pair.id), "source_type": "qa",
                "category": pair.category, "subcategory": pair.subcategory,
            }
            document.embedding = vector
            document.embedding_dimensions = len(vector)
            document.embedding_model = model
            await db.flush()
            await db.execute(update(FileDocument).where(
                FileDocument.id == document.id,
                FileDocument.project_id == project_id,
            ).values(content_tsv=func.to_tsvector(
                literal_column("'pg_catalog.english'::regconfig"), snapshot.content,
            )))
            pair.document_id = document.id
            pair.status = "processed"
            pair.error_message = None
            await db.commit()
            document_id = str(document.id)
        return {"success": True, "qa_pair_id": str(qa_pair_id),
                "document_id": document_id, "vector_id": document_id}
    except Exception as error:
        safe_error = safe_qa_failure(error)
        logger.error(
            "QA processing failed", qa_pair_id=str(qa_pair_id),
            error_type=type(error).__name__, reason=safe_error,
        )
        if snapshot is not None:
            try:
                async with get_db_session() as db:
                    pair = await current_pair(db, qa_pair_id, project_id)
                    if (pair is not None and QASnapshot.capture(pair) == snapshot
                            and pair.status != "processed"):
                        pair.status = "failed"
                        pair.error_message = safe_error
                        await db.commit()
            except Exception as status_error:
                logger.error(
                    "Could not persist QA failure", qa_pair_id=str(qa_pair_id),
                    error_type=type(status_error).__name__,
                )
        return {"success": False, "qa_pair_id": str(qa_pair_id), "error": safe_error}

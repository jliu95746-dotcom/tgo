"""One-query storage gauges from RAG-owned metadata; no customer contents returned."""

import asyncio

from sqlalchemy import text

from ..database import get_db_session
from ..schemas.observability import KnowledgeCounts


COUNTS_QUERY = text(
    """
WITH active_collections AS (
    SELECT id, project_id FROM rag_collections WHERE deleted_at IS NULL
), active_files AS (
    SELECT f.id, f.project_id, f.status FROM rag_files f
    LEFT JOIN active_collections c
      ON c.id = f.collection_id AND c.project_id = f.project_id
    WHERE f.deleted_at IS NULL AND (f.collection_id IS NULL OR c.id IS NOT NULL)
), active_documents AS (
    SELECT d.id, d.embedding IS NOT NULL AS has_embedding FROM rag_file_documents d
    LEFT JOIN active_collections c
      ON c.id = d.collection_id AND c.project_id = d.project_id
    LEFT JOIN active_files f ON f.id = d.file_id AND f.project_id = d.project_id
    WHERE (d.collection_id IS NULL OR c.id IS NOT NULL)
      AND (d.file_id IS NULL OR f.id IS NOT NULL)
)
SELECT
    (SELECT COUNT(*) FROM active_collections) AS collections_total,
    (SELECT COUNT(*) FROM active_files) AS files_total,
    (SELECT COUNT(*) FROM active_files WHERE status = 'completed') AS files_completed,
    (SELECT COUNT(*) FROM active_files WHERE status = 'pending') AS files_pending,
    (SELECT COUNT(*) FROM active_files WHERE status IN
        ('processing', 'chunking_documents', 'generating_embeddings'))
        AS files_processing,
    (SELECT COUNT(*) FROM active_files WHERE status = 'failed') AS files_failed,
    (SELECT COUNT(*) FROM active_documents) AS documents_total,
    (SELECT COUNT(*) FROM active_documents WHERE has_embedding) AS embeddings_total
"""
)


async def collect_knowledge_counts() -> KnowledgeCounts:
    try:
        async with asyncio.timeout(3):
            async with get_db_session() as session:
                result = await session.execute(COUNTS_QUERY)
                counts: KnowledgeCounts = KnowledgeCounts.model_validate(
                    result.mappings().one()
                )
                counts.status = "available"
                return counts
    except Exception as exc:
        return KnowledgeCounts(status="unavailable", error=type(exc).__name__)

"""Minimal governed search fields used by both RAG execution paths."""

from pydantic import BaseModel, Field

from app.schemas.knowledge_evidence import KnowledgeDocument


class AutomaticSearchDocument(KnowledgeDocument):
    document_id: str = Field(min_length=1)
    relevance_score: float = Field(
        default=0.0, ge=0, le=1, allow_inf_nan=False
    )


class AutomaticSearchResponse(BaseModel):
    results: list[AutomaticSearchDocument]

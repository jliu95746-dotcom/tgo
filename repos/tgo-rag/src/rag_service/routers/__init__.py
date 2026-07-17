"""
FastAPI routers for the RAG service.
"""

from . import (
    collections,
    embedding_config,
    files,
    health,
    knowledge_governance,
    monitoring,
    qa,
    websites,
)

__all__ = [
    "collections",
    "files",
    "health",
    "knowledge_governance",
    "monitoring",
    "embedding_config",
    "websites",
    "qa",
]

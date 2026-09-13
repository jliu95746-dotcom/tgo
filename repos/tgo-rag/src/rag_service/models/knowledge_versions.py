"""One stable source and immutable published content snapshots."""
from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class KnowledgeVersionSource(Base, UUIDMixin, TimestampMixin):
    __tablename__ = 'rag_knowledge_version_sources'
    project_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    collection_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey('rag_collections.id', ondelete='CASCADE'), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    active_file_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    active_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    retention: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default='10')
    __table_args__ = (
        UniqueConstraint('project_id', 'source_kind', 'source_id', name='uq_rag_version_source'),
        CheckConstraint("source_kind IN ('file','qa','website')", name='ck_rag_version_source_kind'),
        CheckConstraint('retention BETWEEN 2 AND 100', name='ck_rag_version_retention'),
    )


class KnowledgeVersion(Base, UUIDMixin, TimestampMixin):
    __tablename__ = 'rag_knowledge_versions'
    source_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), ForeignKey('rag_knowledge_version_sources.id', ondelete='CASCADE'), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_action: Mapped[str] = mapped_column(String(16), nullable=False, default='save')
    digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    restored_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        UniqueConstraint('source_id', 'number', name='uq_rag_version_number'),
        CheckConstraint('number > 0', name='ck_rag_version_number'),
        CheckConstraint("state IN ('processing','ready','pending_review','published','retired','failed','unchanged')", name='ck_rag_version_state'),
        CheckConstraint("requested_action IN ('save','submit','publish')", name='ck_rag_version_action'),
    )

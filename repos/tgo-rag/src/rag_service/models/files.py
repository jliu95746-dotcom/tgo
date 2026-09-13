"""
File model for uploaded files.
"""

from typing import List, Optional

from sqlalchemy import ARRAY, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, SoftDeleteMixin, TimestampMixin, UUIDMixin


class File(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """
    File model representing uploaded files for RAG processing.
    
    This model stores metadata about uploaded files and tracks their
    processing status through the document extraction pipeline.
    """

    __tablename__ = "rag_files"

    # Foreign keys
    project_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Associated project ID (logical reference to API service)",
    )

    collection_id: Mapped[Optional[UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rag_collections.id", ondelete="SET NULL"),
        nullable=True,
        doc="Associated collection ID",
    )

    # File information
    original_filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Original filename when uploaded",
    )

    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="File size in bytes",
    )

    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="MIME type of the file",
    )

    # Storage information
    storage_provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="Storage provider (local, s3, gcs, azure)",
    )

    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Path to file in storage system",
    )

    storage_metadata: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        doc="Storage-specific metadata (bucket, region, etc.)",
    )

    # Processing information
    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="pending",
        doc="Processing status (pending, processing, chunking_documents, generating_embeddings, completed, failed, archived)",
    )

    document_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Number of document chunks generated from this file",
    )

    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Safe processing failure reason, retained across worker sessions",
    )

    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Total number of tokens across all document chunks",
    )

    # Content metadata
    language: Mapped[Optional[str]] = mapped_column(
        String(10),
        nullable=True,
        doc="Detected or specified language (ISO 639-1 code)",
    )

    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Optional file description",
    )

    tags: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String),
        nullable=True,
        doc="File tags for categorization and filtering",
    )

    # User information
    uploaded_by: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        doc="User who uploaded the file",
    )

    # Relationships
    collection: Mapped[Optional["Collection"]] = relationship(
        "Collection",
        back_populates="files",
        doc="Associated collection",
    )

    documents: Mapped[List["FileDocument"]] = relationship(
        "FileDocument",
        back_populates="file",
        cascade="all, delete-orphan",
        doc="Document chunks generated from this file",
    )

    # Indexes
    __table_args__ = (
        Index("idx_rag_files_project_id", "project_id"),
        Index("idx_rag_files_collection_id", "collection_id"),
        Index("idx_rag_files_status", "status"),
        Index("idx_rag_files_content_type", "content_type"),
        Index("idx_rag_files_storage_provider", "storage_provider"),
        Index("idx_rag_files_uploaded_by", "uploaded_by"),
        Index("idx_rag_files_language", "language"),
        Index("idx_rag_files_created_at", "created_at"),
        Index("idx_rag_files_project_status", "project_id", "status"),
        Index("idx_rag_files_project_content_type", "project_id", "content_type"),
        Index("idx_rag_files_project_uploaded_by", "project_id", "uploaded_by"),
        Index("idx_rag_files_deleted_at", "deleted_at"),
        Index("idx_rag_files_tags", "tags", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        """String representation of the file."""
        return f"<File(id={self.id}, filename='{self.original_filename}', status='{self.status}')>"

    @property
    def is_processing(self) -> bool:
        """Check if the file is currently being processed."""
        return self.status == "processing"

    @property
    def is_completed(self) -> bool:
        """Check if the file processing is completed."""
        return self.status == "completed"

    @property
    def is_failed(self) -> bool:
        """Check if the file processing failed."""
        return self.status == "failed"

    def update_status(self, status: str) -> None:
        """
        Update the file processing status.
        
        Args:
            status: New status value
        """
        valid_statuses = {"pending", "processing", "chunking_documents", "generating_embeddings", "completed", "failed", "archived"}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")
        self.status = status

    def has_tag(self, tag: str) -> bool:
        """
        Check if the file has a specific tag.

        Args:
            tag: Tag to check for

        Returns:
            True if tag exists, False otherwise
        """
        if not self.tags:
            return False
        return tag in self.tags

    def add_tag(self, tag: str) -> None:
        """
        Add a tag to the file.

        Args:
            tag: Tag to add
        """
        if self.tags is None:
            self.tags = []
        if tag not in self.tags:
            self.tags.append(tag)

    def remove_tag(self, tag: str) -> None:
        """
        Remove a tag from the file.

        Args:
            tag: Tag to remove
        """
        if self.tags and tag in self.tags:
            self.tags.remove(tag)

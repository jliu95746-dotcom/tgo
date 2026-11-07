"""Collection-related Pydantic schemas."""

import uuid
from typing import Any, Dict, Optional

from pydantic import Field

from app.schemas.base import BaseSchema


class CollectionBase(BaseSchema):
    """Base collection schema with common fields."""

    display_name: str = Field(
        max_length=255,
        description="Human-readable collection name",
        examples=["Product Documentation"],
    )
    description: Optional[str] = Field(
        default=None,
        description="Collection description",
        examples=["Documentation for all product features and APIs"],
    )
    collection_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Collection metadata (embedding model, chunk size, etc.)",
        examples=[
            {"embedding_model": "text-embedding-ada-002", "chunk_size": 1000}
        ],
    )


class CollectionCreate(CollectionBase):
    """Schema for creating a new collection."""

    pass


class CollectionUpdate(BaseSchema):
    """Schema for updating an existing collection."""

    display_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Updated collection name",
    )
    description: Optional[str] = Field(
        default=None,
        description="Updated collection description",
    )
    collection_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Updated collection metadata",
    )


class CollectionResponse(BaseSchema):
    """Schema for collection API responses from RAG service."""

    id: uuid.UUID = Field(description="Collection ID")
    display_name: str = Field(description="Human-readable collection name")
    description: Optional[str] = Field(
        default=None, description="Collection description"
    )
    collection_metadata: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Collection metadata (embedding model, chunk size, etc.)",
    )
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last update timestamp")
    deleted_at: Optional[str] = Field(
        default=None, description="Deletion timestamp"
    )
    enabled: Optional[bool] = Field(
        default=None,
        description=(
            'Whether this collection is enabled for the current agent binding'
        ),
    )

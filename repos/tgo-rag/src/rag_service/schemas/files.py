"""
File-related Pydantic schemas.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class FileResponse(BaseModel):
    """Schema for file API responses."""
    
    id: UUID = Field(
        ...,
        description="File unique identifier"
    )
    collection_id: Optional[UUID] = Field(
        None,
        description="Associated collection ID",
        examples=["coll_123e4567-e89b-12d3-a456-426614174000"]
    )
    original_filename: str = Field(
        ...,
        description="Original filename when uploaded",
        examples=["product_manual.pdf"]
    )
    file_size: int = Field(
        ...,
        description="File size in bytes",
        examples=[2048576]
    )
    content_type: str = Field(
        ...,
        description="MIME type of the file",
        examples=["application/pdf"]
    )
    storage_provider: str = Field(
        ...,
        description="Storage provider used",
        examples=["local", "s3", "gcs", "azure"]
    )
    storage_path: str = Field(
        ...,
        description="Path to file in storage system"
    )
    storage_metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Storage-specific metadata"
    )
    status: str = Field(
        ...,
        description="Processing status",
        examples=["pending", "processing", "completed", "failed", "archived"]
    )
    document_count: int = Field(
        ...,
        description="Number of document chunks generated",
        examples=[25]
    )
    error_message: Optional[str] = Field(
        None, description="Safe failure reason when file processing failed"
    )
    total_tokens: int = Field(
        ...,
        description="Total tokens across all document chunks",
        examples=[5000]
    )
    language: Optional[str] = Field(
        None,
        description="Detected or specified language",
        examples=["en", "es", "fr"]
    )
    description: Optional[str] = Field(
        None,
        description="Optional file description"
    )
    tags: Optional[List[str]] = Field(
        None,
        description="File tags for categorization and filtering",
        examples=[["document", "manual", "pdf"]]
    )
    uploaded_by: Optional[str] = Field(
        None,
        description="User who uploaded the file"
    )
    created_at: datetime = Field(
        ...,
        description="File upload timestamp"
    )
    updated_at: datetime = Field(
        ...,
        description="File last update timestamp"
    )
    deleted_at: Optional[datetime] = Field(
        None,
        description="File deletion timestamp (if soft deleted)"
    )

    class Config:
        from_attributes = True


class FileUploadResponse(BaseModel):
    """Schema for file upload responses."""

    id: UUID = Field(
        ...,
        description="File unique identifier"
    )
    original_filename: str = Field(
        ...,
        description="Original filename"
    )
    file_size: int = Field(
        ...,
        description="File size in bytes"
    )
    content_type: str = Field(
        ...,
        description="MIME type of the file"
    )
    status: str = Field(
        ...,
        description="Initial processing status",
        examples=["pending"]
    )
    message: str = Field(
        ...,
        description="Upload status message",
        examples=["File uploaded successfully and queued for processing"]
    )


class FileUploadError(BaseModel):
    """Schema for file upload error details."""

    filename: str = Field(
        ...,
        description="Original filename that failed to upload"
    )
    error_code: str = Field(
        ...,
        description="Error code",
        examples=["FILE_TOO_LARGE", "UNSUPPORTED_TYPE", "INVALID_FILE"]
    )
    error_message: str = Field(
        ...,
        description="Detailed error message"
    )


class BatchUploadSummary(BaseModel):
    """Schema for batch upload summary statistics."""

    total_files: int = Field(
        ...,
        description="Total number of files in the batch",
        examples=[5]
    )
    successful_uploads: int = Field(
        ...,
        description="Number of successfully uploaded files",
        examples=[4]
    )
    failed_uploads: int = Field(
        ...,
        description="Number of failed uploads",
        examples=[1]
    )
    total_size: int = Field(
        ...,
        description="Total size of all files in bytes",
        examples=[10485760]
    )


class BatchFileUploadResponse(BaseModel):
    """Schema for batch file upload responses."""

    summary: BatchUploadSummary = Field(
        ...,
        description="Batch upload summary statistics"
    )
    successful_uploads: List[FileUploadResponse] = Field(
        ...,
        description="List of successfully uploaded files"
    )
    failed_uploads: List[FileUploadError] = Field(
        ...,
        description="List of failed uploads with error details"
    )
    message: str = Field(
        ...,
        description="Overall batch status message",
        examples=["Batch upload completed: 4 successful, 1 failed"]
    )


class FileListResponse(BaseModel):
    """Schema for paginated file list responses."""
    
    data: List[FileResponse] = Field(
        ...,
        description="List of files"
    )
    pagination: "PaginationMetadata" = Field(
        ...,
        description="Pagination metadata"
    )


# Import here to avoid circular imports
from .common import PaginationMetadata
FileListResponse.model_rebuild()

"""RAG Files proxy endpoints."""

from typing import List, Optional
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import StreamingResponse

from app.api.common_responses import (
    CREATE_RESPONSES,
    CRUD_RESPONSES,
    LIST_RESPONSES,
)
from app.core.logging import get_logger
from app.core.security import get_authenticated_project
from app.core.security import AuthenticatedProject
from app.schemas.rag import (
    BatchFileUploadResponse,
    FileListResponse,
    FileResponse,
    FileUploadResponse,
    DocumentListResponse,
)
from app.services.rag_client import rag_client

logger = get_logger("endpoints.rag_files")
router = APIRouter()


@router.get(
    "",
    response_model=FileListResponse,
    responses=LIST_RESPONSES,
    summary="List Files",
    description="""
    Retrieve all files for the authenticated project with filtering and pagination.

    Files represent uploaded documents that are processed for RAG operations.
    All results are automatically scoped to the authenticated project.
    """,
)
async def list_files(
    collection_id: Optional[UUID] = Query(
        None, description="Filter by collection ID"
    ),
    status: Optional[str] = Query(
        None, description="Filter by processing status"
    ),
    content_type: Optional[str] = Query(
        None, description="Filter by MIME type"
    ),
    uploaded_by: Optional[str] = Query(None, description="Filter by uploader"),
    tags: Optional[str] = Query(
        None, description="Filter by tags (comma-separated list)"
    ),
    limit: int = Query(
        20, ge=1, le=100, description="Number of files to return"
    ),
    offset: int = Query(0, ge=0, description="Number of files to skip"),
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> FileListResponse:
    """List files from RAG service."""
    logger.info(
        "Listing RAG files",
        extra={
            "collection_id": str(collection_id) if collection_id else None,
            "status": status,
            "content_type": content_type,
            "uploaded_by": uploaded_by,
            "tags": tags,
            "limit": limit,
            "offset": offset,
        },
    )
    project, _api_key = project_and_key
    project_id = str(project.id)

    result = await rag_client.list_files(
        project_id=project_id,
        collection_id=str(collection_id) if collection_id else None,
        status=status,
        content_type=content_type,
        uploaded_by=uploaded_by,
        tags=tags,
        limit=limit,
        offset=offset,
    )

    return FileListResponse.model_validate(result)


@router.post(
    "",
    response_model=FileUploadResponse,
    responses={
        **CREATE_RESPONSES,
        413: {"description": "File too large - exceeds maximum allowed size"},
        415: {"description": "Unsupported media type - file type not allowed"},
    },
    status_code=201,
    summary="Upload File",
    description="""
    Upload a new file for RAG processing within the authenticated project.

    The file will be stored and queued for document extraction and embedding generation.
    Supported formats: PDF, Word documents, text files, and markdown files.
    All files are automatically scoped to the authenticated project.
    """,
)
async def upload_file(
    file: UploadFile = File(
        ..., description="File to upload for RAG processing"
    ),
    collection_id: Optional[UUID] = Form(
        None, description="Collection ID to associate with the file"
    ),
    description: Optional[str] = Form(
        None, description="Optional file description"
    ),
    language: Optional[str] = Form(
        None, description="Document language (ISO 639-1 code)"
    ),
    tags: Optional[str] = Form(
        None, description="Comma-separated list of tags"
    ),
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> FileUploadResponse:
    """Upload file to RAG service."""
    logger.info(
        "Uploading RAG file",
        extra={
            "file_name": file.filename,
            "content_type": file.content_type,
            "collection_id": str(collection_id) if collection_id else None,
            "tags": tags,
        },
    )
    project, _api_key = project_and_key
    project_id = str(project.id)

    result = await rag_client.upload_file(
        project_id=project_id,
        file=file,
        collection_id=str(collection_id) if collection_id else None,
        description=description,
        language=language,
        tags=tags,
    )

    return FileUploadResponse.model_validate(result)


@router.post(
    "/batch",
    response_model=BatchFileUploadResponse,
    responses={
        **CREATE_RESPONSES,
        413: {
            "description": "One or more files too large - exceeds maximum allowed size"
        },
        415: {
            "description": "Unsupported media type - one or more file types not allowed"
        },
    },
    status_code=201,
    summary="Batch Upload Files",
    description="""
    Upload multiple files for RAG processing within the authenticated project in a single batch operation.

    All files will be stored and queued for document extraction and embedding generation.
    The response includes details about successful uploads and any failures.
    Supported formats: PDF, Word documents, text files, and markdown files.
    All files are automatically scoped to the authenticated project.
    """,
)
async def upload_files_batch(
    files: List[UploadFile] = File(
        ..., description="List of files to upload for RAG processing"
    ),
    collection_id: UUID = Form(
        ..., description="Collection ID to associate with all files"
    ),
    description: Optional[str] = Form(
        None, description="Optional description applied to all files"
    ),
    language: Optional[str] = Form(
        None,
        description="Document language (ISO 639-1 code) applied to all files",
    ),
    tags: Optional[str] = Form(
        None, description="Comma-separated list of tags applied to all files"
    ),
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> BatchFileUploadResponse:
    """Upload multiple files to RAG service in a batch."""
    logger.info(
        "Batch uploading RAG files",
        extra={
            "file_count": len(files),
            "filenames": [f.filename for f in files],
            "collection_id": str(collection_id),
            "tags": tags,
        },
    )
    project, _api_key = project_and_key
    project_id = str(project.id)

    result = await rag_client.upload_files_batch(
        project_id=project_id,
        files=files,
        collection_id=str(collection_id),
        description=description,
        language=language,
        tags=tags,
    )

    return BatchFileUploadResponse.model_validate(result)


@router.get(
    "/{file_id}",
    response_model=FileResponse,
    responses=CRUD_RESPONSES,
    summary="Get File",
    description="""
    Retrieve detailed information about a specific file including processing status
    and metadata about generated documents. File must belong to the authenticated project.
    """,
)
async def get_file(
    file_id: UUID,
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> FileResponse:
    """Get file from RAG service."""
    logger.info("Getting RAG file", extra={"file_id": str(file_id)})

    project, _api_key = project_and_key
    project_id = str(project.id)

    result = await rag_client.get_file(
        project_id=project_id,
        file_id=str(file_id),
    )

    return FileResponse.model_validate(result)


@router.get(
    "/{file_id}/download",
    responses={
        200: {
            "description": "File content",
            "content": {"application/octet-stream": {}},
        },
        401: {
            "description": "Unauthorized - invalid or missing authentication"
        },
        403: {
            "description": "Forbidden - file doesn't belong to user's project"
        },
        404: {"description": "File not found or not accessible"},
        500: {"description": "Internal server error - file system error"},
        502: {"description": "Bad Gateway - RAG service unavailable"},
        504: {"description": "Gateway Timeout - RAG service timeout"},
    },
    summary="Download File",
    description="""
    Download a file by its ID with appropriate headers for browser download.

    Downloads the original file content with proper Content-Type, Content-Disposition,
    and other headers for browser compatibility. The file must belong to the authenticated project.

    Security features:
    - Validates file belongs to authenticated project
    - Prevents unauthorized access to files
    - Verifies file exists and is not deleted
    - Supports Unicode filenames with proper encoding
    """,
)
async def download_file(
    file_id: UUID,
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> StreamingResponse:
    """Download file from RAG service."""
    logger.info("Downloading RAG file", extra={"file_id": str(file_id)})
    try:
        # Get the raw response from RAG service

        project, _api_key = project_and_key
        project_id = str(project.id)
        response = await rag_client.download_file(
            project_id=project_id,
            file_id=str(file_id),
        )

        # Handle error responses from RAG service

        if not response.is_success:
            try:
                error_data = response.json()
            except:
                error_data = {
                    "error": {"message": response.text or "Unknown error"}
                }
            logger.warning(
                f"RAG service download error: {response.status_code}",
                extra={
                    "file_id": str(file_id),
                    "status_code": response.status_code,
                    "error_data": error_data,
                },
            )

            raise HTTPException(
                status_code=response.status_code, detail=error_data
            )
        # Extract headers from RAG service response

        content_type = response.headers.get(
            "content-type", "application/octet-stream"
        )
        content_disposition = response.headers.get("content-disposition")
        content_length = response.headers.get("content-length")

        # Prepare headers for client response

        headers = {}
        if content_disposition:
            headers["Content-Disposition"] = content_disposition
        if content_length:
            headers["Content-Length"] = content_length
        logger.info(
            "RAG file download successful",
            extra={
                "file_id": str(file_id),
                "content_type": content_type,
                "content_length": content_length,
            },
        )

        # Stream the file content back to client

        return StreamingResponse(
            content=response.iter_bytes(chunk_size=8192),
            media_type=content_type,
            headers=headers,
        )
    except HTTPException:
        # Re-raise HTTP exceptions (already handled)

        raise
    except Exception as e:
        logger.error(
            f"Unexpected error during file download: {e}",
            extra={"file_id": str(file_id), "error": str(e)},
        )
        raise HTTPException(
            status_code=500,
            detail="Internal server error during file download",
        )


@router.delete(
    "/{file_id}",
    responses=CRUD_RESPONSES,
    status_code=204,
    summary="Delete File",
    description="""
    Delete a specific file by its UUID. This will also delete all associated
    document chunks and remove the file from storage. File must belong to the authenticated project.
    """,
)
async def delete_file(
    file_id: UUID,
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> None:
    """Delete file from RAG service."""
    logger.info("Deleting RAG file", extra={"file_id": str(file_id)})
    project, _api_key = project_and_key
    project_id = str(project.id)

    await rag_client.delete_file(
        project_id=project_id,
        file_id=str(file_id),
    )


@router.get(
    "/{file_id}/documents",
    response_model=DocumentListResponse,
    responses=LIST_RESPONSES,
    summary="List File Documents",
    description="""
    Retrieve all document chunks generated from a specific file with filtering and pagination.
    
    This endpoint provides access to the processed document chunks for RAG operations.
    File must belong to the authenticated project.
    """,
)
async def list_file_documents(
    file_id: UUID,
    content_type: Optional[str] = Query(
        None, description="Filter by content type"
    ),
    chunk_index: Optional[int] = Query(
        None, description="Filter by chunk index"
    ),
    limit: int = Query(
        20, ge=1, le=100, description="Number of documents to return"
    ),
    offset: int = Query(0, ge=0, description="Number of documents to skip"),
    project_and_key: AuthenticatedProject = Depends(get_authenticated_project),
) -> DocumentListResponse:
    """List documents for a file from RAG service."""
    logger.info(
        "Listing file documents",
        extra={
            "file_id": str(file_id),
            "content_type": content_type,
            "chunk_index": chunk_index,
            "limit": limit,
            "offset": offset,
        },
    )
    project, _api_key = project_and_key
    project_id = str(project.id)

    result = await rag_client.list_file_documents(
        project_id=project_id,
        file_id=str(file_id),
        content_type=content_type,
        chunk_index=chunk_index,
        limit=limit,
        offset=offset,
    )

    return DocumentListResponse.model_validate(result)

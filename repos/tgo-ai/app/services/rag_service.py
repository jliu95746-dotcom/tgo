"""RAG Service client for external collection data retrieval."""

import uuid
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.exceptions import NotFoundError, ValidationError
from app.schemas.knowledge import KnowledgeChannel


class CollectionData(BaseModel):
    """Collection data from RAG service."""

    id: uuid.UUID = Field(description="Collection ID")
    display_name: str = Field(description="Human-readable collection name")
    description: Optional[str] = Field(default=None, description="Collection description")
    collection_metadata: Optional[Dict] = Field(default=None, description="Collection metadata")
    created_at: str = Field(description="Creation timestamp")
    updated_at: str = Field(description="Last update timestamp")
    deleted_at: Optional[str] = Field(default=None, description="Deletion timestamp")


class CollectionBatchRequest(BaseModel):
    """Request model for batch collection retrieval."""

    collection_ids: List[str] = Field(description="List of collection IDs to retrieve")


class CollectionBatchResponse(BaseModel):
    """Response model for batch collection retrieval."""

    collections: List[CollectionData] = Field(description="Found collections")
    not_found: List[str] = Field(description="Collection IDs that were not found")


class RAGServiceClient:
    """Client for interacting with the external RAG service."""

    def __init__(self):
        """Initialize the RAG service client."""
        self.base_url = settings.rag_service_url
        self.timeout = 30.0

    async def get_collections_batch(
        self,
        collection_ids: List[str],
        project_id: str
    ) -> CollectionBatchResponse:
        """
        Retrieve multiple collections by their IDs from the RAG service.

        Args:
            collection_ids: List of collection ID strings
            project_id: Project ID

        Returns:
            CollectionBatchResponse with found collections and not found IDs

        Raises:
            ValidationError: If RAG service returns validation error
            NotFoundError: If RAG service is unavailable
        """
        if not collection_ids:
            return CollectionBatchResponse(collections=[], not_found=[])

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/collections/batch",
                    params={"project_id": project_id},
                    json={"collection_ids": collection_ids},
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    data = response.json()
                    return CollectionBatchResponse(**data)
                elif response.status_code == 400:
                    raise ValidationError(
                        "Bad request to RAG service",
                        "collections",
                        {"status_code": response.status_code}
                    )
                elif response.status_code == 422:
                    error_data = response.json()
                    raise ValidationError(
                        f"Invalid collection IDs: {error_data.get('detail', 'Unknown validation error')}",
                        "collections",
                        {"status_code": response.status_code, "detail": error_data}
                    )
                else:
                    response.raise_for_status()

            except httpx.RequestError as e:
                raise NotFoundError(
                    "RAG Service",
                    f"Unable to connect to RAG service: {str(e)}"
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # All collections not found
                    return CollectionBatchResponse(
                        collections=[],
                        not_found=collection_ids
                    )
                else:
                    raise ValidationError(
                        f"RAG service error: {e.response.status_code}",
                        "collections",
                        {"status_code": e.response.status_code}
                    )

    async def validate_collections_exist(
        self,
        collection_ids: List[str],
        project_id: str
    ) -> None:
        """
        Validate that collections exist in the RAG service.

        Args:
            collection_ids: List of collection ID strings to validate
            project_id: Project ID

        Raises:
            NotFoundError: If any collection is not found
            ValidationError: If validation fails
        """
        if not collection_ids:
            return

        batch_response = await self.get_collections_batch(collection_ids, project_id)

        if batch_response.not_found:
            raise NotFoundError(
                "Collection",
                f"Collections not found: {', '.join(batch_response.not_found)}"
            )

    async def get_collection(
        self,
        collection_id: str,
        project_id: str
    ) -> Optional[CollectionData]:
        """
        Retrieve a single collection by ID from the RAG service.

        Args:
            collection_id: Collection ID string
            project_id: Project ID

        Returns:
            CollectionData if found, None otherwise

        Raises:
            ValidationError: If RAG service returns validation error
        """
        batch_response = await self.get_collections_batch([collection_id], project_id)

        if batch_response.collections:
            return batch_response.collections[0]
        return None

    async def search_documents(
        self,
        collection_id: str,
        project_id: str | uuid.UUID,
        query: str,
        knowledge_channel: KnowledgeChannel | str,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Search documents in a collection.

        Args:
            collection_id: Collection ID string
            project_id: Project ID string or UUID
            query: Search query
            limit: Maximum number of results

        Returns:
            Search results dictionary
        """
        try:
            channel = KnowledgeChannel(knowledge_channel)
        except ValueError as exc:
            raise ValidationError(
                "A supported knowledge channel is required for RAG search",
                "knowledge_channel",
            ) from exc

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    (
                        f"{self.base_url}/v1/collections/{collection_id}"
                        "/documents/search/automatic-answer"
                    ),
                    params={"project_id": str(project_id)},
                    json={
                        "query": query,
                        "limit": limit,
                        "min_score": settings.rag_min_similarity_score,
                        "channel": channel.value,
                    },
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise ValidationError(
                    f"RAG search error: {e.response.status_code}",
                    "collections",
                    {"status_code": e.response.status_code, "detail": e.response.text}
                )
            except httpx.RequestError as e:
                raise NotFoundError(
                    "RAG Service",
                    f"Unable to connect to RAG service for search: {str(e)}"
                )


    async def batch_sync_embedding_configs(
        self,
        configs: List["EmbeddingConfigCreate"],
    ) -> "EmbeddingConfigBatchSyncResponse":
        """Batch upsert embedding configurations in the RAG service.

        Maps to POST /v1/embedding-configs/batch-sync (no auth).
        """
        # Early return if nothing to sync
        if not configs:
            return EmbeddingConfigBatchSyncResponse(
                success_count=0, failed_count=0, errors=[]
            )

        payload = {"configs": [c.model_dump(mode="json", exclude_none=True) for c in configs]}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/v1/embedding-configs/batch-sync",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    data = response.json()
                    return EmbeddingConfigBatchSyncResponse(**data)
                elif response.status_code == 422:
                    # Validation error from RAG service; include details
                    data = response.json()
                    raise ValidationError(
                        "Invalid embedding config payload",
                        "embedding-configs",
                        {"detail": data},
                    )
                else:
                    response.raise_for_status()

            except httpx.RequestError as e:
                raise NotFoundError(
                    "RAG Service",
                    f"Unable to connect to RAG service for embedding sync: {str(e)}",
                )
            except httpx.HTTPStatusError as e:
                # Bubble up as validation error with status
                raise ValidationError(
                    f"RAG service error during embedding sync: {e.response.status_code}",
                    "embedding-configs",
                    {"status_code": e.response.status_code},
                )


# Global RAG service client instance
rag_service_client = RAGServiceClient()



class EmbeddingConfigCreate(BaseModel):
    """Embedding configuration item to sync to RAG service."""

    project_id: uuid.UUID
    provider: str
    model: str
    # Optional extras supported by RAG; we pass when available
    dimensions: Optional[int] = Field(default=None)
    batch_size: Optional[int] = Field(default=None)
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    is_active: Optional[bool] = True


class EmbeddingConfigBatchSyncRequest(BaseModel):
    configs: List[EmbeddingConfigCreate]


class EmbeddingConfigBatchSyncResponse(BaseModel):
    success_count: int
    failed_count: int
    errors: Optional[List[Dict]] = None

"""RAG Service client for external collection data retrieval."""

import uuid
from collections import Counter
from typing import List, Optional

from app.services.service_identity import rag_service_headers
import httpx
from pydantic import BaseModel, Field, JsonValue, TypeAdapter

from app.config import settings
from app.exceptions import NotFoundError, ValidationError
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.knowledge_availability import KnowledgeAvailability
from app.schemas.rag_client import AutomaticSearchResponse

SAFE_EMBEDDING_ERROR = "Embedding configuration synchronization failed"
_json_object = TypeAdapter(dict[str, JsonValue])


def _rag_object(response: httpx.Response, field: str) -> dict[str, JsonValue]:
    if response.status_code != 200:
        raise ValidationError(
            f"RAG service error: {response.status_code}",
            field,
            {"status_code": response.status_code},
        )
    return _json_object.validate_python(response.json())


def _same_resource_id(left: str, right: str) -> bool:
    try:
        return uuid.UUID(left) == uuid.UUID(right)
    except ValueError:
        return left == right


class CollectionData(BaseModel):
    """Collection data from RAG service."""

    id: uuid.UUID = Field(description="Collection ID")
    display_name: str = Field(description="Human-readable collection name")
    description: Optional[str] = Field(default=None, description="Collection description")
    collection_metadata: Optional[dict[str, JsonValue]] = Field(
        default=None, description="Collection metadata"
    )
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


def _check_collection_batch(
    result: CollectionBatchResponse, collection_ids: List[str]
) -> None:
    requested = Counter(uuid.UUID(value) for value in collection_ids)
    found = [row.id for row in result.collections]
    missing = Counter(uuid.UUID(value) for value in result.not_found)
    if (
        len(found) != len(set(found))
        or set(found) & missing.keys()
        or set(found) | missing.keys() != requested.keys()
        or any(count > requested[value] for value, count in missing.items())
    ):
        raise ValueError("Collection batch does not match the requested IDs")


class RAGServiceClient:
    """Client for interacting with the external RAG service."""

    def __init__(self) -> None:
        """Initialize the RAG service client."""
        self.base_url = settings.rag_service_url
        self.timeout = 30.0

    async def knowledge_availability(
        self, project_id: str, channel: KnowledgeChannel
    ) -> KnowledgeAvailability:
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=rag_service_headers()
            ) as client:
                response = await client.get(
                    f"{self.base_url}/v1/knowledge-governance/availability",
                    params={"project_id": project_id, "channel": channel.value},
                )
                result = KnowledgeAvailability.model_validate(
                    _rag_object(response, "knowledge")
                )
                identifiers = [row.id for row in result.collections]
                if (
                    result.project_id != uuid.UUID(project_id)
                    or result.channel != channel
                    or len(identifiers) != len(set(identifiers))
                    or any(
                        row.eligible_chunk_count > row.total_chunk_count
                        for row in result.collections
                    )
                ):
                    raise ValueError("Knowledge availability scope mismatch")
                return result
        except (httpx.RequestError, httpx.InvalidURL):
            raise NotFoundError("RAG Service") from None
        except ValueError:
            raise ValidationError(
                "Invalid knowledge availability response", "knowledge"
            ) from None

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

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=rag_service_headers()
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/collections/batch",
                    params={"project_id": project_id},
                    json={"collection_ids": collection_ids},
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code == 200:
                    result = CollectionBatchResponse.model_validate(
                        response.json()
                    )
                    _check_collection_batch(result, collection_ids)
                    return result
                if response.status_code == 404:
                    return CollectionBatchResponse(
                        collections=[], not_found=collection_ids
                    )
                if response.status_code == 400:
                    message = "Bad request to RAG service"
                elif response.status_code == 422:
                    message = "Invalid collection IDs"
                else:
                    message = f"RAG service error: {response.status_code}"
                raise ValidationError(
                    message, "collections",
                    {"status_code": response.status_code},
                )
        except httpx.RequestError:
            raise NotFoundError("RAG Service") from None
        except ValueError:
            raise ValidationError(
                "Invalid collection batch response", "collections"
            ) from None

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
                details={"missing_ids": batch_response.not_found},
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
    ) -> dict[str, JsonValue]:
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
        except ValueError:
            raise ValidationError(
                "A supported knowledge channel is required for RAG search",
                "knowledge_channel",
            ) from None

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=rag_service_headers()
            ) as client:
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
                data = _rag_object(response, "collections")
                result = AutomaticSearchResponse.model_validate(data)
                if any(
                    not _same_resource_id(row.collection_id, collection_id)
                    for row in result.results
                ):
                    raise ValueError("Knowledge search scope mismatch")
                return data
        except (httpx.RequestError, httpx.InvalidURL):
            raise NotFoundError("RAG Service") from None
        except ValueError:
            raise ValidationError(
                "Invalid RAG search response", "collections"
            ) from None


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

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout, headers=rag_service_headers()
            ) as client:
                response = await client.post(
                    f"{self.base_url}/v1/embedding-configs/batch-sync",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                result = EmbeddingConfigBatchSyncResponse.model_validate(
                    _rag_object(response, "embedding-configs")
                )
                return validate_embedding_receipt(result, configs)
        except (httpx.RequestError, httpx.InvalidURL):
            raise NotFoundError("RAG Service") from None
        except ValueError:
            raise ValidationError(
                "Invalid embedding synchronization response", "embedding-configs"
            ) from None


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
    success_count: int = Field(ge=0, strict=True)
    failed_count: int = Field(ge=0, strict=True)
    errors: Optional[List[dict[str, JsonValue]]] = None


def validate_embedding_receipt(
    receipt: EmbeddingConfigBatchSyncResponse,
    configs: List[EmbeddingConfigCreate],
) -> EmbeddingConfigBatchSyncResponse:
    """Check item accounting before exposing or persisting sync results."""
    try:
        requested = Counter(item.project_id for item in configs)
        errors = receipt.errors or []
        if (
            type(receipt.success_count) is not int
            or type(receipt.failed_count) is not int
            or receipt.success_count < 0
            or receipt.failed_count < 0
            or receipt.success_count + receipt.failed_count != len(configs)
            or receipt.failed_count != len(errors)
        ):
            raise ValueError("Invalid embedding batch counts")
        failures: Counter[uuid.UUID] = Counter()
        safe_errors: list[dict[str, JsonValue]] = []
        for error in errors:
            value = error.get("project_id")
            if not isinstance(value, str):
                raise ValueError("Missing failed project identity")
            project_id = uuid.UUID(value)
            failures[project_id] += 1
            if failures[project_id] > requested[project_id]:
                raise ValueError("Unexpected failed project identity")
            message = error.get("message")
            if message != (
                "Invalid dimensions: only 1536 is supported in phase 1"
            ):
                message = SAFE_EMBEDDING_ERROR
            safe_errors.append({"project_id": value, "message": message})
        return receipt.model_copy(
            update={
                "errors": safe_errors if receipt.errors is not None else None,
            }
        )
    except (ValueError, TypeError):
        raise ValidationError(
            "Invalid embedding synchronization receipt", "embedding-configs"
        ) from None

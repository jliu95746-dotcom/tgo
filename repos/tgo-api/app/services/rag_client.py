"""Business methods for the configured RAG HTTP service."""

from collections.abc import Mapping
from typing import List, Optional

from fastapi import HTTPException, UploadFile

from app.schemas.scenario_resources import KnowledgeAvailability
from app.services.rag_transport import JsonObject, RAGTransport, RequestParams


class RAGServiceClient(RAGTransport):
    """Forward tenant-scoped knowledge operations to the RAG service."""

    async def knowledge_availability(
        self, project_id: str, channel: str
    ) -> KnowledgeAvailability:
        response = await self._make_request(
            "GET",
            "/v1/knowledge-governance/availability",
            params={"project_id": project_id, "channel": channel},
        )
        return KnowledgeAvailability.model_validate(
            await self._object_response(response)
        )

    async def knowledge_version_request(
        self,
        method: str,
        path: str,
        project_id: str,
        payload: Mapping[str, object] | None = None,
    ) -> JsonObject:
        response = await self._make_request(
            method,
            "/v1/knowledge-versions" + path,
            params={"project_id": project_id},
            json_data=payload,
        )
        return await self._object_response(response)

    async def upload_knowledge_replacement(
        self, project_id: str, reference: str, actor: str, file: UploadFile
    ) -> JsonObject:
        content = await file.read(100 * 1024 * 1024 + 1)
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(413, "文件超过 100MB 限制。")
        response = await self._make_request(
            "POST",
            f"/v1/knowledge-versions/file/{reference}/upload",
            params={"project_id": project_id},
            data={"actor": actor},
            files={"file": (file.filename, content, file.content_type)},
        )
        return await self._object_response(response)

    async def list_collections(
        self,
        project_id: str,
        display_name: Optional[str] = None,
        collection_type: Optional[str] = None,
        tags: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonObject:
        """List collections from RAG service."""
        params: RequestParams = {"limit": limit, "offset": offset}
        if display_name:
            params["display_name"] = display_name
        if collection_type:
            params["collection_type"] = collection_type
        if tags:
            params["tags"] = tags
        params["project_id"] = project_id

        response = await self._make_request(
            "GET", "/v1/collections", params=params
        )
        return await self._paginated_response(response)

    async def create_collection(
        self,
        project_id: str,
        collection_data: Mapping[str, object],
    ) -> JsonObject:
        """Create collection in RAG service."""
        response = await self._make_request(
            "POST",
            "/v1/collections",
            json_data=collection_data,
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def get_collection(
        self,
        project_id: str,
        collection_id: str,
        include_stats: bool = False,
    ) -> JsonObject:
        """Get collection from RAG service."""
        params: RequestParams = {
            "include_stats": include_stats,
            "project_id": project_id,
        }
        response = await self._make_request(
            "GET", f"/v1/collections/{collection_id}", params=params
        )
        return await self._object_response(response)

    async def update_collection(
        self,
        project_id: str,
        collection_id: str,
        collection_data: Mapping[str, object],
    ) -> JsonObject:
        """Update collection in RAG service."""
        response = await self._make_request(
            "PUT",
            f"/v1/collections/{collection_id}",
            json_data=collection_data,
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def delete_collection(
        self,
        project_id: str,
        collection_id: str,
    ) -> None:
        """Delete collection from RAG service."""
        response = await self._make_request(
            "DELETE",
            f"/v1/collections/{collection_id}",
            params={"project_id": project_id},
        )
        await self._handle_response(response)

    async def search_collection_documents(
        self,
        project_id: str,
        collection_id: str,
        search_data: Mapping[str, object],
    ) -> JsonObject:
        """Search documents in collection."""
        response = await self._make_request(
            "POST",
            f"/v1/collections/{collection_id}/documents/search",
            json_data=search_data,
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def update_knowledge_channels(
        self, *, project_id: str, record_id: str, data: Mapping[str, object]
    ) -> JsonObject:
        response = await self._make_request(
            "PATCH",
            f"/v1/knowledge-governance/{record_id}/channels",
            params={"project_id": project_id},
            json_data=data,
        )
        return await self._object_response(response)

    async def list_knowledge_governance(
        self,
        *,
        project_id: str,
        collection_id: str,
        review_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JsonObject:
        """List governed files in a collection."""
        params: RequestParams = {
            "project_id": project_id,
            "collection_id": collection_id,
            "limit": limit,
            "offset": offset,
        }
        if review_status is not None:
            params["review_status"] = review_status
        response = await self._make_request(
            "GET",
            "/v1/knowledge-governance",
            params=params,
        )
        return await self._object_response(response)

    async def save_file_knowledge_governance(
        self,
        *,
        project_id: str,
        file_id: str,
        data: Mapping[str, object],
    ) -> JsonObject:
        """Create or update a file governance draft."""
        response = await self._make_request(
            "PUT",
            f"/v1/knowledge-governance/files/{file_id}",
            params={"project_id": project_id},
            json_data=data,
        )
        return await self._object_response(response)

    async def save_qa_knowledge_governance(
        self,
        *,
        project_id: str,
        qa_pair_id: str,
        data: Mapping[str, object],
    ) -> JsonObject:
        """Create or update tenant-scoped QA governance metadata."""
        response = await self._make_request(
            "PUT",
            f"/v1/knowledge-governance/qa-pairs/{qa_pair_id}",
            params={"project_id": project_id},
            json_data=data,
        )
        return await self._object_response(response)

    async def submit_knowledge_governance(
        self,
        *,
        project_id: str,
        record_id: str,
    ) -> JsonObject:
        """Submit a governance draft for review."""
        response = await self._make_request(
            "POST",
            f"/v1/knowledge-governance/{record_id}/submit",
            params={"project_id": project_id},
        )
        return await self._object_response(response)

    async def review_knowledge_governance(
        self,
        *,
        project_id: str,
        record_id: str,
        decision: Mapping[str, object],
    ) -> JsonObject:
        """Forward an authenticated review decision."""
        response = await self._make_request(
            "POST",
            f"/v1/knowledge-governance/{record_id}/review",
            params={"project_id": project_id},
            json_data=decision,
        )
        return await self._object_response(response)

    async def backfill_knowledge_governance(
        self,
        *,
        project_id: str,
        data: Mapping[str, object],
    ) -> JsonObject:
        """Dry-run or execute safe governance backfill for legacy files."""
        response = await self._make_request(
            "POST",
            "/v1/knowledge-governance/backfill",
            params={"project_id": project_id},
            json_data=data,
        )
        return await self._object_response(response)

    async def list_collection_pages(
        self,
        project_id: str,
        collection_id: str,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonObject:
        """List crawled pages for a website collection."""
        params: RequestParams = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status

        response = await self._make_request(
            "GET", f"/v1/collections/{collection_id}/pages", params=params
        )
        return await self._paginated_response(response)

    async def list_files(
        self,
        project_id: str,
        collection_id: Optional[str] = None,
        status: Optional[str] = None,
        content_type: Optional[str] = None,
        uploaded_by: Optional[str] = None,
        tags: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonObject:
        """List files from RAG service."""
        params: RequestParams = {"limit": limit, "offset": offset}
        if collection_id:
            params["collection_id"] = collection_id
        if status:
            params["status"] = status
        if content_type:
            params["content_type"] = content_type
        if uploaded_by:
            params["uploaded_by"] = uploaded_by
        if tags:
            params["tags"] = tags
        params["project_id"] = project_id

        response = await self._make_request("GET", "/v1/files", params=params)
        return await self._paginated_response(response)

    async def upload_file(
        self,
        project_id: str,
        file: UploadFile,
        collection_id: Optional[str] = None,
        description: Optional[str] = None,
        language: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> JsonObject:
        """Upload file to RAG service."""
        files = {"file": (file.filename, file.file, file.content_type)}
        data = {"project_id": project_id}

        if collection_id:
            data["collection_id"] = collection_id
        if description:
            data["description"] = description
        if language:
            data["language"] = language
        if tags:
            data["tags"] = tags

        response = await self._make_request(
            "POST", "/v1/files", files=files, data=data
        )
        return await self._object_response(response)

    async def get_file(
        self,
        project_id: str,
        file_id: str,
    ) -> JsonObject:
        """Get file from RAG service."""
        response = await self._make_request(
            "GET", f"/v1/files/{file_id}", params={"project_id": project_id}
        )
        return await self._object_response(response)

    async def delete_file(
        self,
        project_id: str,
        file_id: str,
    ) -> None:
        """Delete file from RAG service."""
        response = await self._make_request(
            "DELETE", f"/v1/files/{file_id}", params={"project_id": project_id}
        )
        await self._handle_response(response)

    async def upload_files_batch(
        self,
        project_id: str,
        files: List[UploadFile],
        collection_id: str,
        description: Optional[str] = None,
        language: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> JsonObject:
        """Upload multiple files to RAG service in a batch."""
        # Prepare files for multipart upload
        files_data = []
        for file in files:
            files_data.append(
                ("files", (file.filename, file.file, file.content_type))
            )

        data = {"collection_id": collection_id, "project_id": project_id}

        if description:
            data["description"] = description
        if language:
            data["language"] = language
        if tags:
            data["tags"] = tags

        response = await self._make_request(
            "POST", "/v1/files/batch", files=files_data, data=data
        )
        return await self._object_response(response)

    async def list_file_documents(
        self,
        project_id: str,
        file_id: str,
        content_type: Optional[str] = None,
        chunk_index: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonObject:
        """List chunks/documents for a specific file."""
        params: RequestParams = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
        if content_type:
            params["content_type"] = content_type
        if chunk_index is not None:
            params["chunk_index"] = chunk_index

        response = await self._make_request(
            "GET", f"/v1/files/{file_id}/documents", params=params
        )
        return await self._object_response(response)

    async def list_website_pages(
        self,
        project_id: str,
        collection_id: str,
        status: Optional[str] = None,
        depth: Optional[int] = None,
        parent_page_id: Optional[str] = None,
        tree_depth: Optional[int] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> JsonObject:
        """List all pages in a collection.

        Args:
            tree_depth: Number of child levels to include.
                - 0 or None: Flat list (no children)
                - 1: Include direct children only
                - 2: Include children and grandchildren
                - -1: Include all descendants (unlimited depth)
        """
        params: RequestParams = {
            "project_id": project_id,
            "collection_id": collection_id,
            "limit": limit,
            "offset": offset,
        }
        if status:
            params["status"] = status
        if depth is not None:
            params["depth"] = depth
        if parent_page_id:
            params["parent_page_id"] = parent_page_id
        if tree_depth is not None:
            params["tree_depth"] = tree_depth

        response = await self._make_request(
            "GET", "/v1/websites/pages", params=params
        )
        return await self._object_response(response)

    async def get_website_page(
        self,
        project_id: str,
        page_id: str,
    ) -> JsonObject:
        """Get details of a specific page."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "GET", f"/v1/websites/pages/{page_id}", params=params
        )
        return await self._object_response(response)

    async def add_website_page(
        self,
        project_id: str,
        collection_id: str,
        page_data: Mapping[str, object],
    ) -> JsonObject:
        """Add a page to crawl in a collection."""
        params: RequestParams = {
            "project_id": project_id,
            "collection_id": collection_id,
        }
        response = await self._make_request(
            "POST", "/v1/websites/pages", params=params, json_data=page_data
        )
        return await self._object_response(response)

    async def delete_website_page(
        self,
        project_id: str,
        page_id: str,
    ) -> None:
        """Delete a page from the collection."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "DELETE", f"/v1/websites/pages/{page_id}", params=params
        )
        await self._handle_response(response)

    async def recrawl_website_page(
        self,
        project_id: str,
        page_id: str,
    ) -> JsonObject:
        """Trigger re-crawling of an existing page."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "POST", f"/v1/websites/pages/{page_id}/recrawl", params=params
        )
        return await self._object_response(response)

    async def crawl_deeper_from_page(
        self,
        project_id: str,
        page_id: str,
        crawl_data: Mapping[str, object],
    ) -> JsonObject:
        """Extract page links and add them to the crawl queue."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/websites/pages/{page_id}/crawl-deeper",
            params=params,
            json_data=crawl_data,
        )
        return await self._object_response(response)

    async def get_crawl_progress(
        self,
        project_id: str,
        collection_id: str,
    ) -> JsonObject:
        """Get crawl progress for a collection."""
        params: RequestParams = {
            "project_id": project_id,
            "collection_id": collection_id,
        }
        response = await self._make_request(
            "GET", "/v1/websites/progress", params=params
        )
        return await self._object_response(response)

    async def create_qa_pair(
        self,
        project_id: str,
        collection_id: str,
        qa_data: Mapping[str, object],
    ) -> JsonObject:
        """Create a single QA pair in a collection."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/collections/{collection_id}/qa-pairs",
            params=params,
            json_data=qa_data,
        )
        return await self._object_response(response)

    async def list_qa_pairs(
        self,
        project_id: str,
        collection_id: str,
        limit: int = 20,
        offset: int = 0,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> JsonObject:
        """List QA pairs in a collection."""
        params: RequestParams = {
            "project_id": project_id,
            "limit": limit,
            "offset": offset,
        }
        if category:
            params["category"] = category
        if status:
            params["status"] = status

        response = await self._make_request(
            "GET", f"/v1/collections/{collection_id}/qa-pairs", params=params
        )
        return await self._object_response(response)

    async def batch_create_qa_pairs(
        self,
        project_id: str,
        collection_id: str,
        qa_data: Mapping[str, object],
    ) -> JsonObject:
        """Batch create QA pairs in a collection."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/collections/{collection_id}/qa-pairs/batch",
            params=params,
            json_data=qa_data,
        )
        return await self._object_response(response)

    async def import_qa_pairs(
        self,
        project_id: str,
        collection_id: str,
        import_data: Mapping[str, object],
    ) -> JsonObject:
        """Import QA pairs from JSON/CSV."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "POST",
            f"/v1/collections/{collection_id}/qa-pairs/import",
            params=params,
            json_data=import_data,
        )
        return await self._object_response(response)

    async def get_qa_pair(
        self,
        project_id: str,
        qa_pair_id: str,
    ) -> JsonObject:
        """Get a single QA pair by ID."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "GET", f"/v1/qa-pairs/{qa_pair_id}", params=params
        )
        return await self._object_response(response)

    async def update_qa_pair(
        self,
        project_id: str,
        qa_pair_id: str,
        qa_data: Mapping[str, object],
    ) -> JsonObject:
        """Update a QA pair."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "PUT",
            f"/v1/qa-pairs/{qa_pair_id}",
            params=params,
            json_data=qa_data,
        )
        return await self._object_response(response)

    async def delete_qa_pair(
        self,
        project_id: str,
        qa_pair_id: str,
    ) -> None:
        """Delete a QA pair."""
        params: RequestParams = {"project_id": project_id}
        response = await self._make_request(
            "DELETE", f"/v1/qa-pairs/{qa_pair_id}", params=params
        )
        await self._handle_response(response)

    async def list_qa_categories(
        self,
        project_id: str,
        collection_id: Optional[str] = None,
    ) -> JsonObject:
        """Get distinct QA pair categories for a project."""
        params: RequestParams = {"project_id": project_id}
        if collection_id:
            params["collection_id"] = collection_id

        response = await self._make_request(
            "GET", "/v1/qa-categories", params=params
        )
        return await self._object_response(response)


rag_client = RAGServiceClient()

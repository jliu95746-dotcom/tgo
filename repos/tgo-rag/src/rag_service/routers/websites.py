"""
Website crawling endpoints.

This module provides API endpoints for website page crawling operations.
Crawl configuration is stored in Collection.crawl_config.
"""

import fnmatch
import hashlib
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db_session_dependency
from ..logging_config import get_logger
from ..models import Collection, File as FileModel, FileDocument, WebsitePage
from ..schemas.common import ErrorResponse, PaginationMetadata
from ..schemas.websites import (
    AddPageRequest,
    AddPageResponse,
    CrawlDeeperRequest,
    CrawlDeeperResponse,
    CrawlProgressSchema,
    WebsitePageListResponse,
    WebsitePageResponse,
)

router = APIRouter()
logger = get_logger(__name__)

# Completed statuses for tree_completed calculation
COMPLETED_STATUSES = {"processed", "skipped", "failed"}


def compute_url_hash(url: str) -> str:
    """Generate SHA-256 hash of URL for deduplication."""
    return hashlib.sha256(url.encode()).hexdigest()


async def check_tree_completed(
    db: AsyncSession,
    page_id: UUID,
    page_status: str,
) -> bool:
    """
    Check if a page and all its descendant pages have completed processing.

    A page tree is considered "completed" when:
    - The page itself has status: processed, skipped, or failed
    - All descendant pages (children, grandchildren, etc.) have one of these statuses

    Args:
        db: Database session
        page_id: UUID of the page to check
        page_status: Current status of the page

    Returns:
        True if the entire tree is completed, False otherwise
    """
    # If the page itself is not completed, the tree is not completed
    if page_status not in COMPLETED_STATUSES:
        return False

    # Use recursive CTE to find all descendant pages
    # Check if any descendant is not in completed status
    from sqlalchemy import text

    query = text("""
        WITH RECURSIVE descendants AS (
            -- Direct children
            SELECT id, status
            FROM rag_website_pages
            WHERE parent_page_id = :page_id

            UNION ALL

            -- Recursive: children of children
            SELECT p.id, p.status
            FROM rag_website_pages p
            INNER JOIN descendants d ON p.parent_page_id = d.id
        )
        SELECT COUNT(*) as incomplete_count
        FROM descendants
        WHERE status NOT IN ('processed', 'skipped', 'failed')
    """)

    result = await db.execute(query, {"page_id": page_id})
    row = result.fetchone()
    incomplete_count = row[0] if row else 0

    return incomplete_count == 0


async def batch_check_tree_completed(
    db: AsyncSession,
    pages: List,
) -> Dict[UUID, bool]:
    """
    Batch check tree_completed status for multiple pages.

    This is more efficient than checking each page individually when
    listing multiple pages.

    Args:
        db: Database session
        pages: List of WebsitePage objects

    Returns:
        Dictionary mapping page_id to tree_completed status
    """
    if not pages:
        return {}

    result_map: Dict[UUID, bool] = {}

    # First, mark pages that are not themselves completed
    for page in pages:
        if page.status not in COMPLETED_STATUSES:
            result_map[page.id] = False

    # For pages that are completed, check their descendants
    completed_pages = [p for p in pages if p.id not in result_map]

    if not completed_pages:
        return result_map

    # Check each completed page individually using the single-page function
    # This avoids complexity with array parameters in raw SQL
    for page in completed_pages:
        tree_completed = await check_tree_completed(db, page.id, page.status)
        result_map[page.id] = tree_completed

    return result_map


def _build_page_dict(
    page,
    tree_completed: bool,
    has_children: bool,
    children: Optional[List] = None,
) -> dict:
    """Build a page dictionary from a WebsitePage model."""
    return {
        "id": page.id,
        "collection_id": page.collection_id,
        "parent_page_id": page.parent_page_id,
        "url": page.url,
        "title": page.title,
        "depth": page.depth,
        "content_length": page.content_length or 0,
        "meta_description": page.meta_description,
        "status": page.status,
        "crawl_source": page.crawl_source,
        "http_status_code": page.http_status_code,
        "file_id": page.file_id,
        "discovered_links": page.discovered_links,
        "error_message": page.error_message,
        "tree_completed": tree_completed,
        "has_children": has_children,
        "children": children,
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    }


async def load_descendants(
    db: AsyncSession,
    parent_ids: List[UUID],
    max_depth: int,
    current_depth: int = 0,
) -> Dict[UUID, List]:
    """
    Load descendants for a list of parent page IDs.

    Args:
        db: Database session
        parent_ids: List of parent page IDs to load children for
        max_depth: Maximum depth to load (-1 for unlimited)
        current_depth: Current recursion depth

    Returns:
        Dictionary mapping parent_page_id to list of child WebsitePage objects
    """
    if not parent_ids:
        return {}

    # Check if we've reached the depth limit
    if max_depth != -1 and current_depth >= max_depth:
        return {}

    # Load direct children
    query = select(WebsitePage).where(
        WebsitePage.parent_page_id.in_(parent_ids)
    ).order_by(WebsitePage.created_at.desc())

    result = await db.execute(query)
    children = list(result.scalars().all())

    if not children:
        return {}

    # Group children by parent
    children_by_parent: Dict[UUID, List] = {}
    for child in children:
        if child.parent_page_id not in children_by_parent:
            children_by_parent[child.parent_page_id] = []
        children_by_parent[child.parent_page_id].append(child)

    # Recursively load grandchildren if needed
    if max_depth == -1 or current_depth + 1 < max_depth:
        child_ids = [child.id for child in children]
        grandchildren_map = await load_descendants(
            db, child_ids, max_depth, current_depth + 1
        )
        # Attach grandchildren info to children
        for child in children:
            child._grandchildren = grandchildren_map.get(child.id, [])
    else:
        for child in children:
            child._grandchildren = []

    return children_by_parent


async def batch_check_has_children(
    db: AsyncSession,
    page_ids: List[UUID],
) -> Dict[UUID, bool]:
    """
    Batch check which pages have children.

    Args:
        db: Database session
        page_ids: List of page IDs to check

    Returns:
        Dictionary mapping page_id to has_children boolean
    """
    if not page_ids:
        return {}

    # Query to find which page_ids have at least one child
    query = (
        select(WebsitePage.parent_page_id)
        .where(WebsitePage.parent_page_id.in_(page_ids))
        .distinct()
    )

    result = await db.execute(query)
    parent_ids_with_children = {row[0] for row in result.fetchall()}

    return {page_id: page_id in parent_ids_with_children for page_id in page_ids}


async def build_page_tree(
    db: AsyncSession,
    pages: List,
    tree_depth: Optional[int],
    tree_completed_map: Dict[UUID, bool],
) -> List[WebsitePageResponse]:
    """
    Build a tree structure from a list of pages.

    Args:
        db: Database session
        pages: List of root-level WebsitePage objects
        tree_depth: How many levels of children to include (None/0 = none, -1 = unlimited)
        tree_completed_map: Pre-computed tree_completed values for all pages

    Returns:
        List of WebsitePageResponse with children populated
    """
    if not pages:
        return []

    # Get page IDs for has_children check
    page_ids = [page.id for page in pages]

    # If no tree depth requested, return flat list
    if tree_depth is None or tree_depth == 0:
        # Check which pages have children
        has_children_map = await batch_check_has_children(db, page_ids)

        return [
            WebsitePageResponse(**_build_page_dict(
                page,
                tree_completed_map.get(page.id, False),
                has_children_map.get(page.id, False),
                children=None
            ))
            for page in pages
        ]

    # Load descendants
    children_map = await load_descendants(db, page_ids, tree_depth, 0)

    # Collect all loaded pages for tree_completed and has_children calculation
    all_descendant_pages = []
    all_descendant_ids = []

    def collect_descendants(children_by_parent: Dict[UUID, List]):
        for children_list in children_by_parent.values():
            for child in children_list:
                all_descendant_pages.append(child)
                all_descendant_ids.append(child.id)
                if hasattr(child, '_grandchildren') and child._grandchildren:
                    # Recursively collect from grandchildren
                    gc_map = {child.id: child._grandchildren}
                    collect_descendants(gc_map)

    collect_descendants(children_map)

    # Calculate tree_completed for all descendant pages
    descendant_tree_completed = await batch_check_tree_completed(db, all_descendant_pages)

    # Calculate has_children for all pages (root + descendants)
    all_page_ids = page_ids + all_descendant_ids
    has_children_map = await batch_check_has_children(db, all_page_ids)

    # Merge with the original map
    full_tree_completed_map = {**tree_completed_map, **descendant_tree_completed}

    def build_response_tree(page) -> WebsitePageResponse:
        """Recursively build response tree for a page."""
        children_list = children_map.get(page.id, [])
        has_children = has_children_map.get(page.id, False)

        if not children_list:
            return WebsitePageResponse(**_build_page_dict(
                page,
                full_tree_completed_map.get(page.id, False),
                has_children,
                children=[]
            ))

        # Build children recursively
        child_responses = []
        for child in children_list:
            # Get grandchildren from the attached attribute
            grandchildren = getattr(child, '_grandchildren', [])
            if grandchildren:
                # Temporarily update children_map for recursive call
                children_map[child.id] = grandchildren
            child_responses.append(build_response_tree(child))

        return WebsitePageResponse(**_build_page_dict(
            page,
            full_tree_completed_map.get(page.id, False),
            has_children,
            children=child_responses
        ))

    return [build_response_tree(page) for page in pages]


async def check_url_exists_in_collection(
    db: AsyncSession,
    collection_id: UUID,
    url_hash: str,
) -> Tuple[bool, Optional[str]]:
    """
    Check if a URL already exists in the collection's pages.

    Returns:
        Tuple of (exists: bool, status: Optional[str])
        - If exists=True, status contains the current page status
        - If exists=False, status is None
    """
    query = select(WebsitePage.status).where(
        and_(
            WebsitePage.collection_id == collection_id,
            WebsitePage.url_hash == url_hash,
        )
    )
    result = await db.execute(query)
    page_status = result.scalar_one_or_none()

    if page_status is not None:
        return (True, page_status)
    return (False, None)


async def check_urls_exist_in_collection(
    db: AsyncSession,
    collection_id: UUID,
    url_hashes: List[str],
) -> set:
    """
    Check which URLs already exist in the collection's pages.

    Returns:
        Set of url_hashes that already exist
    """
    if not url_hashes:
        return set()

    query = select(WebsitePage.url_hash).where(
        and_(
            WebsitePage.collection_id == collection_id,
            WebsitePage.url_hash.in_(url_hashes),
        )
    )
    result = await db.execute(query)
    existing_hashes = {row[0] for row in result.fetchall()}
    return existing_hashes


async def _build_progress(db: AsyncSession, collection_id: UUID) -> CrawlProgressSchema:
    """Build progress schema by querying page statuses."""
    # Count pages by status
    query = select(
        WebsitePage.status,
        func.count(WebsitePage.id)
    ).where(
        WebsitePage.collection_id == collection_id
    ).group_by(WebsitePage.status)

    result = await db.execute(query)
    status_counts = dict(result.fetchall())

    total = sum(status_counts.values())
    pending = status_counts.get("pending", 0) + status_counts.get("crawling", 0)
    crawled = status_counts.get("fetched", 0) + status_counts.get("extracted", 0)
    processed = status_counts.get("processed", 0)
    skipped = status_counts.get("skipped", 0)
    failed = status_counts.get("failed", 0)
    processing = status_counts.get("processing", 0)

    # Include skipped pages in progress since they are completed work
    # (URLs intentionally not crawled due to exclude patterns, depth limits, etc.)
    completed = crawled + processed + skipped + failed
    progress_percent = (completed / total * 100) if total > 0 else 0

    return CrawlProgressSchema(
        total_pages=total,
        pages_pending=pending,
        pages_processing=processing,
        pages_crawled=crawled + processed + skipped,  # Include skipped as "crawled" (completed)
        pages_processed=processed + skipped,  # Skipped pages count as processed (no further work needed)
        pages_failed=failed,
        progress_percent=min(progress_percent, 100.0),
    )


async def _delete_file_and_documents(
    db: AsyncSession,
    file_id: UUID,
    project_id: UUID,
    collection_id: UUID,
) -> Optional[str]:
    """
    Delete a File and all its associated FileDocument records.

    Args:
        db: Database session
        file_id: UUID of the file to delete

    Returns:
        Storage path of the deleted file (for physical file cleanup), or None if file not found
    """
    # Get file record
    file_query = select(FileModel).where(
        FileModel.id == file_id, FileModel.project_id == project_id,
        FileModel.collection_id == collection_id,
    ).with_for_update()
    file_result = await db.execute(file_query)
    file_record = file_result.scalar_one_or_none()

    if not file_record:
        return None

    storage_path = (
        file_record.storage_path if file_record.storage_provider == "local" else None
    )

    # Delete associated FileDocument records first
    await db.execute(
        delete(FileDocument).where(
            FileDocument.file_id == file_id, FileDocument.project_id == project_id,
            FileDocument.collection_id == collection_id,
        )
    )
    logger.debug(f"Deleted FileDocument records for file {file_id}")

    # Delete the file record
    await db.delete(file_record)
    logger.debug(f"Deleted file record {file_id}")

    return storage_path


async def _delete_page_cascade(
    db: AsyncSession,
    page: WebsitePage,
) -> List[Tuple[UUID, str]]:
    """
    Delete a WebsitePage and its associated File/FileDocument records.

    Args:
        db: Database session
        page: WebsitePage to delete
    """
    storage_files: List[Tuple[UUID, str]] = []
    # Failed recrawls can have both a candidate and an older published file.
    result = await db.execute(select(FileModel.id).where(
        FileModel.project_id == page.project_id,
        FileModel.collection_id == page.collection_id,
        or_(
            FileModel.id == page.file_id,
            and_(
                FileModel.storage_metadata["source"].astext == "website_crawl",
                FileModel.storage_metadata["page_id"].astext == str(page.id),
            ),
        ),
    ))
    for file_id in result.scalars().all():
        storage_path = await _delete_file_and_documents(
            db, file_id, page.project_id, page.collection_id,
        )
        if storage_path:
            storage_files.append((file_id, storage_path))
    await db.delete(page)
    return storage_files


def cleanup_deleted_page_files(storage_files: List[Tuple[UUID, str]]) -> None:
    """Called only after commit; reject paths outside generated UUID storage."""
    from ..config import get_settings
    from ..services.website_documents import owned_storage_path

    for file_id, storage_path in storage_files:
        try:
            owned_storage_path(
                get_settings().upload_dir, file_id, storage_path,
            ).unlink(missing_ok=True)
        except (OSError, ValueError) as error:
            logger.warning("Deleted page file cleanup failed", file_id=str(file_id),
                           error_type=type(error).__name__)


# ============================================================================
# Page List Endpoints
# ============================================================================

@router.get(
    "/pages",
    response_model=WebsitePageListResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Collection not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_pages(
    collection_id: UUID = Query(..., description="Collection ID"),
    status: Optional[str] = Query(None, description="Filter by page status"),
    depth: Optional[int] = Query(None, description="Filter by depth"),
    parent_page_id: Optional[UUID] = Query(None, description="Filter by parent page ID"),
    tree_depth: Optional[int] = Query(
        None,
        ge=-1,
        le=10,
        description="Number of child levels to include. 0/None=no children, 1=direct children, -1=unlimited",
    ),
    limit: int = Query(20, ge=1, le=100, description="Number of pages to return"),
    offset: int = Query(0, ge=0, description="Number of pages to skip"),
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    List all pages in a collection.

    When tree_depth is specified, the response includes child pages in a hierarchical structure.
    - tree_depth=0 or None: Flat list (no children)
    - tree_depth=1: Include direct children only
    - tree_depth=2: Include children and grandchildren
    - tree_depth=-1: Include all descendants (unlimited depth)

    When no parent_page_id filter is specified and tree_depth > 0, returns root pages
    (where parent_page_id IS NULL) with their children.
    """
    # Verify collection exists
    coll_query = select(Collection).where(
        and_(
            Collection.id == collection_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
    )
    coll_result = await db.execute(coll_query)
    if not coll_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection not found")

    # Query pages
    query = select(WebsitePage).where(WebsitePage.collection_id == collection_id)

    if status:
        query = query.where(WebsitePage.status == status)
    if depth is not None:
        query = query.where(WebsitePage.depth == depth)

    # Handle parent_page_id filtering
    # When tree_depth > 0 and no parent_page_id specified, default to root pages
    if parent_page_id is not None:
        query = query.where(WebsitePage.parent_page_id == parent_page_id)
    elif tree_depth is not None and tree_depth != 0:
        # Default to root pages when building tree without explicit parent filter
        query = query.where(WebsitePage.parent_page_id.is_(None))

    # Get total count (only counts root-level pages in tree mode)
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Apply pagination
    query = query.offset(offset).limit(limit).order_by(WebsitePage.created_at.desc())

    result = await db.execute(query)
    pages = list(result.scalars().all())

    # Batch calculate tree_completed for root pages
    tree_completed_map = await batch_check_tree_completed(db, pages)

    # Build response with tree structure if tree_depth is specified
    page_responses = await build_page_tree(db, pages, tree_depth, tree_completed_map)

    pagination = PaginationMetadata(
        total=total,
        limit=limit,
        offset=offset,
        has_next=offset + limit < total,
        has_prev=offset > 0,
    )

    return WebsitePageListResponse(data=page_responses, pagination=pagination)


@router.get(
    "/pages/{page_id}",
    response_model=WebsitePageResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Page not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_page(
    page_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Get details of a specific page.
    """
    query = select(WebsitePage).where(
        and_(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
        )
    )

    result = await db.execute(query)
    page = result.scalar_one_or_none()

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Calculate tree_completed and has_children for this page
    tree_completed = await check_tree_completed(db, page.id, page.status)
    has_children_map = await batch_check_has_children(db, [page.id])
    has_children = has_children_map.get(page.id, False)

    # Build response
    return WebsitePageResponse(**_build_page_dict(
        page,
        tree_completed,
        has_children,
        children=None
    ))


# ============================================================================
# Add Page Endpoints
# ============================================================================

@router.post(
    "/pages",
    response_model=AddPageResponse,
    status_code=201,
    responses={
        404: {"model": ErrorResponse, "description": "Collection not found"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_page(
    request: AddPageRequest,
    collection_id: UUID = Query(..., description="Target collection ID"),
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Add a page to crawl in a collection.

    This will create a page record and trigger crawling.
    If max_depth > 0, discovered links will also be crawled.

    Deduplication:
    - If the URL already exists in the collection, it will be skipped
    """
    # Lock the collection so manual adds share quota/dedup with discovery.
    coll_query = select(Collection).where(
        and_(
            Collection.id == collection_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
    )
    coll_result = await db.execute(coll_query.with_for_update())
    collection = coll_result.scalar_one_or_none()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    url = str(request.url)
    url_hash = compute_url_hash(url)

    # Check if URL already exists in collection
    exists, existing_status = await check_url_exists_in_collection(
        db, collection_id, url_hash
    )

    if exists:
        if existing_status in ("pending", "crawling", "fetched"):
            return AddPageResponse(
                success=False,
                page_id=None,
                message=f"URL is currently being crawled (status: {existing_status})",
                status="crawling",
            )
        else:
            return AddPageResponse(
                success=False,
                page_id=None,
                message=f"URL already exists in collection (status: {existing_status})",
                status="exists",
            )

    page_count = await db.scalar(select(func.count()).select_from(WebsitePage).where(
        WebsitePage.collection_id == collection_id,
        WebsitePage.project_id == project_id,
    ))
    if (page_count or 0) >= (collection.crawl_config or {}).get("max_pages", 100):
        return AddPageResponse(
            success=False, page_id=None, status="limit_reached",
            message="已达到这个知识库的页面数量上限，请调整上限或删除不需要的页面。",
        )

    # Validate parent page if provided
    parent_page = None
    page_depth = 0
    if request.parent_page_id:
        parent_query = select(WebsitePage).where(
            and_(
                WebsitePage.id == request.parent_page_id,
                WebsitePage.collection_id == collection_id,
            )
        )
        parent_result = await db.execute(parent_query)
        parent_page = parent_result.scalar_one_or_none()

        if not parent_page:
            raise HTTPException(
                status_code=400,
                detail="Parent page not found in this collection"
            )

        page_depth = parent_page.depth + 1

    # Only explicitly supplied fields override inherited settings.
    page_config = dict(parent_page.crawl_config or {}) if parent_page else {}
    if request.options is not None:
        page_config.update(request.options.model_dump(
            exclude_none=True, exclude_unset=True,
        ))
        if request.options.headers is not None:
            page_config["headers_origin"] = url
    if request.include_patterns is not None:
        page_config["include_patterns"] = request.include_patterns
    if request.exclude_patterns is not None:
        page_config["exclude_patterns"] = request.exclude_patterns
    absolute_max_depth = page_depth + request.max_depth
    page_config["max_depth"] = absolute_max_depth

    # Create new page record
    new_page = WebsitePage(
        collection_id=collection_id,
        project_id=project_id,
        parent_page_id=request.parent_page_id,
        url=url,
        url_hash=url_hash,
        depth=page_depth,
        status="pending",
        crawl_source="manual",
        content_length=0,
        crawl_config=page_config,
    )

    db.add(new_page)
    await db.commit()
    await db.refresh(new_page)

    logger.info(f"Added page {url} to collection {collection_id}")

    # Trigger crawl task
    from ..tasks.website_crawling import crawl_page_task
    try:
        task = crawl_page_task.delay(
            str(new_page.id),
            auto_discover=(request.max_depth > 0),
            max_depth=absolute_max_depth,
        )
        logger.info(f"Triggered crawl page task {task.id} for page {new_page.id}")
    except Exception as error:
        logger.warning("Failed to dispatch added page", page_id=str(new_page.id),
                       error_type=type(error).__name__)
        message = "抓取任务未能提交，请稍后点击重新抓取。"
        await db.execute(update(WebsitePage).where(
            WebsitePage.id == new_page.id, WebsitePage.project_id == project_id,
            WebsitePage.status == "pending",
        ).values(status="failed", error_message=message))
        await db.commit()
        raise HTTPException(status_code=503, detail=message) from error

    return AddPageResponse(
        success=True,
        page_id=new_page.id,
        message="Page added to crawl queue successfully",
        status="added",
    )


@router.delete(
    "/pages/{page_id}",
    status_code=204,
    responses={
        404: {"model": ErrorResponse, "description": "Page not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_page(
    page_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Delete a page from the collection.

    This performs a hard delete that also removes:
    - Associated File record (if any)
    - Associated FileDocument records (if any)
    - Physical file from storage (if any)
    """
    query = select(WebsitePage).join(
        Collection, Collection.id == WebsitePage.collection_id,
    ).where(
        and_(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
            Collection.project_id == project_id,
        )
    ).with_for_update(of=(Collection, WebsitePage))

    result = await db.execute(query)
    page = result.scalar_one_or_none()

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Delete page with cascade (File, FileDocument, physical file)
    storage_files = await _delete_page_cascade(db, page)
    await db.commit()
    cleanup_deleted_page_files(storage_files)

    logger.info(f"Deleted page {page_id} with associated files")


@router.post(
    "/pages/{page_id}/recrawl",
    response_model=AddPageResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Page not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def recrawl_page(
    page_id: UUID,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Trigger re-crawling of an existing page.
    """
    query = select(WebsitePage).join(
        Collection, Collection.id == WebsitePage.collection_id,
    ).where(
        and_(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
    ).with_for_update(of=(Collection, WebsitePage))

    result = await db.execute(query)
    page = result.scalar_one_or_none()

    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    if page.status not in COMPLETED_STATUSES:
        raise HTTPException(status_code=409, detail="这个页面正在处理中，请完成后再重新抓取。")

    # Reset page status
    from ..services.knowledge_versions import find_source
    if await find_source(db, project_id, 'website', page_id):
        raise HTTPException(409, '这份资料已启用版本管理，请使用“更新与历史版本”检查更新。')
    page.status = "pending"
    page.error_message = None
    await db.commit()

    # Trigger crawl task
    from ..tasks.website_crawling import crawl_page_task
    try:
        task = crawl_page_task.delay(str(page_id), auto_discover=False)
        logger.info(f"Triggered recrawl task {task.id} for page {page_id}")
    except Exception as error:
        logger.warning("Failed to trigger recrawl task", page_id=str(page_id),
                       error_type=type(error).__name__)
        message = "抓取任务未能提交，请稍后点击重新抓取。"
        # An uncertain broker response must not overwrite an already claimed task.
        await db.execute(update(WebsitePage).where(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
            WebsitePage.status == "pending",
        ).values(status="failed", error_message=message))
        await db.commit()
        raise HTTPException(status_code=503, detail=message) from error

    return AddPageResponse(
        success=True,
        page_id=page_id,
        message="Page queued for re-crawling",
        status="added",
    )


# ============================================================================
# Progress Endpoint
# ============================================================================

@router.get(
    "/progress",
    response_model=CrawlProgressSchema,
    responses={
        404: {"model": ErrorResponse, "description": "Collection not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_crawl_progress(
    collection_id: UUID = Query(..., description="Collection ID"),
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Get crawl progress for a collection.
    """
    # Verify collection exists
    coll_query = select(Collection).where(
        and_(
            Collection.id == collection_id,
            Collection.project_id == project_id,
            Collection.deleted_at.is_(None),
        )
    )
    coll_result = await db.execute(coll_query)
    if not coll_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Collection not found")

    return await _build_progress(db, collection_id)


@router.post(
    "/pages/{page_id}/crawl-deeper",
    response_model=CrawlDeeperResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Page not found"},
        400: {"model": ErrorResponse, "description": "Invalid request"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def crawl_deeper_from_page(
    page_id: UUID,
    request: CrawlDeeperRequest,
    project_id: UUID = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db_session_dependency),
):
    """
    Extract links from an existing page and add them to the crawl queue.

    This endpoint allows deep crawling from a specific page that has already
    been crawled. It extracts all links from the page's content and adds
    new URLs to the crawl queue.

    Deduplication rules:
    - URLs already in the collection are skipped
    - URLs currently being crawled are skipped
    - New pages have depth = source_page.depth + 1
    - Pages exceeding max_depth are not added
    """
    # Get the source page
    page_query = select(WebsitePage).where(
        and_(
            WebsitePage.id == page_id,
            WebsitePage.project_id == project_id,
        )
    )
    page_result = await db.execute(page_query)
    source_page = page_result.scalar_one_or_none()

    if not source_page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Get collection for configuration
    coll_query = select(Collection).where(
        Collection.id == source_page.collection_id,
        Collection.project_id == project_id, Collection.deleted_at.is_(None),
    )
    coll_result = await db.execute(coll_query)
    collection = coll_result.scalar_one_or_none()

    if not collection:
        raise HTTPException(status_code=404, detail="Collection not found")

    # Get crawl config from collection
    crawl_config = {
        **(collection.crawl_config or {}), **(source_page.crawl_config or {}),
    }

    # Check if page has content or discovered_links
    has_discovered_links = source_page.discovered_links and len(source_page.discovered_links) > 0
    if not source_page.content_markdown and not has_discovered_links:
        return CrawlDeeperResponse(
            success=True,
            source_page_id=page_id,
            pages_added=0,
            pages_skipped=0,
            links_found=0,
            message="Page has no content or discovered links to extract from",
            added_urls=[],
        )

    # Calculate new depth and check max_depth limit
    new_depth = source_page.depth + 1
    max_allowed_depth = source_page.depth + request.max_depth

    if new_depth > max_allowed_depth:
        return CrawlDeeperResponse(
            success=True,
            source_page_id=page_id,
            pages_added=0,
            pages_skipped=0,
            links_found=0,
            message="Max depth reached, no pages added",
            added_urls=[],
        )

    # Extract links from discovered_links or content
    # Parse base domain from source page URL
    parsed_source = urlparse(source_page.url)
    base_domain = parsed_source.netloc

    raw_links = []

    # First, use discovered_links if available (from crawl4ai)
    if source_page.discovered_links:
        for link_info in source_page.discovered_links:
            if isinstance(link_info, dict):
                raw_links.append(link_info.get("url", ""))
            elif isinstance(link_info, str):
                raw_links.append(link_info)

    # If no discovered_links, extract from content
    if not raw_links and source_page.content_markdown:
        # Simple regex for href extraction
        href_pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
        # Also look for markdown links
        md_link_pattern = re.compile(r'\[([^\]]*)\]\(([^)]+)\)', re.IGNORECASE)

        # Extract from HTML-style hrefs (if any HTML remnants)
        for match in href_pattern.finditer(source_page.content_markdown):
            raw_links.append(match.group(1))

        # Extract from markdown links
        for match in md_link_pattern.finditer(source_page.content_markdown):
            raw_links.append(match.group(2))

    # Also check page_metadata for stored links
    if source_page.page_metadata and "links" in source_page.page_metadata:
        for link in source_page.page_metadata.get("links", []):
            if isinstance(link, str):
                raw_links.append(link)
            elif isinstance(link, dict):
                raw_links.append(link.get("href", ""))

    # Normalize and filter links
    def normalize_url(url: str, base_url: str) -> Optional[str]:
        """Normalize and validate URL."""
        try:
            if not url or url.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                return None

            # Handle relative URLs
            if not url.startswith(('http://', 'https://')):
                url = urljoin(base_url, url)

            parsed = urlparse(url)

            # Remove fragments
            url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                url += f"?{parsed.query}"

            # Only allow same domain
            if (parsed.netloc != base_domain
                    and not crawl_config.get("follow_external_links", False)):
                return None

            return url
        except Exception:
            return None

    def should_crawl(url: str) -> bool:
        """Check if URL matches include/exclude patterns."""
        # Use request patterns if provided, otherwise use collection config
        exclude_patterns = (request.exclude_patterns if request.exclude_patterns is not None
                            else crawl_config.get("exclude_patterns", []))
        include_patterns = (request.include_patterns if request.include_patterns is not None
                            else crawl_config.get("include_patterns", []))

        for pattern in exclude_patterns:
            if fnmatch.fnmatch(url, pattern):
                return False

        if include_patterns:
            return any(fnmatch.fnmatch(url, p) for p in include_patterns)

        return True

    # Process and filter links
    valid_links = []
    for link in raw_links:
        normalized = normalize_url(link, source_page.url)
        if normalized and should_crawl(normalized):
            valid_links.append(normalized)

    valid_links = list(dict.fromkeys(valid_links))
    links_found = len(valid_links)

    if not valid_links:
        return CrawlDeeperResponse(
            success=True,
            source_page_id=page_id,
            pages_added=0,
            pages_skipped=0,
            links_found=0,
            message="No valid links found in page",
            added_urls=[],
        )

    from ..services.website_discovery import CrawlOverrides, reserve_child_pages
    page_crawl_config: CrawlOverrides = {}
    if request.exclude_patterns is not None:
        page_crawl_config["exclude_patterns"] = request.exclude_patterns
    if request.include_patterns is not None:
        page_crawl_config["include_patterns"] = request.include_patterns
    reserved = await reserve_child_pages(
        db, page_id, project_id, valid_links, max_allowed_depth,
        overrides=page_crawl_config, crawl_source="deep_crawl",
    )
    added_urls, added_page_ids = reserved.urls, reserved.page_ids
    skipped_count = reserved.skipped
    await db.commit()

    logger.info(
        f"Added {len(added_urls)} pages from deep crawl of page {page_id}, "
        f"skipped {skipped_count} existing URLs, max_allowed_depth={max_allowed_depth}"
    )

    # Trigger crawl tasks for each new page
    from ..tasks.website_crawling import crawl_page_task
    failed_dispatches = 0
    for new_page_id in added_page_ids:
        try:
            task = crawl_page_task.delay(
                str(new_page_id),
                auto_discover=True,
                max_depth=max_allowed_depth,
            )
            logger.debug(f"Triggered crawl page task {task.id} for page {new_page_id}")
        except Exception as error:
            failed_dispatches += 1
            logger.warning("Failed to dispatch deep crawl", page_id=str(new_page_id),
                           error_type=type(error).__name__)
            await db.execute(update(WebsitePage).where(
                WebsitePage.id == new_page_id, WebsitePage.project_id == project_id,
                WebsitePage.status == "pending",
            ).values(status="failed", error_message="抓取任务未能提交，请稍后重新抓取。"))
    if failed_dispatches:
        await db.commit()

    return CrawlDeeperResponse(
        success=not failed_dispatches,
        source_page_id=page_id,
        pages_added=len(added_urls),
        pages_skipped=skipped_count,
        links_found=links_found,
        message=(f"已添加 {len(added_urls)} 个页面。"
                 + ("已达到页面数量上限。" if reserved.limit_reached else "")
                 + (f"其中 {failed_dispatches} 个任务未能提交，请稍后重试。"
                    if failed_dispatches else "")),
        added_urls=added_urls,
    )


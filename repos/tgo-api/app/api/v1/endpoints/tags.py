"""Tag endpoints."""

from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.api.current_access_route import CurrentAccessRoute
from app.services.visitor_tag_access import (
    begin_tag_change,
    begin_tag_removal,
    commit_tag_change,
)
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import (
    get_current_active_user,
    get_user_language,
    UserLanguage,
    require_permission,
)
from app.models import Staff, Tag
from app.schemas.base import PaginationMetadata
from app.schemas import (
    TagCreate,
    TagListParams,
    TagListResponse,
    TagResponse,
    TagUpdate,
    VisitorTagCreate,
    VisitorTagResponse,
)
from app.schemas.tag import set_tag_display_name, set_tag_list_display_name

logger = get_logger("endpoints.tags")
router = APIRouter(route_class=CurrentAccessRoute)
original_bearer = HTTPBearer(auto_error=False)


@router.get("", response_model=TagListResponse)
async def list_tags(
    params: TagListParams = Depends(),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:list")),
    user_language: UserLanguage = Depends(get_user_language),
) -> TagListResponse:
    """
    List tags.
    
    Retrieve a paginated list of tags with optional filtering by category and search.
    Requires tags:list permission.
    """
    logger.info(f"User {current_user.username} listing tags")

    # Build query
    query = db.query(Tag).filter(
        Tag.project_id == current_user.project_id, Tag.deleted_at.is_(None)
    )

    # Apply filters
    if params.category:
        query = query.filter(Tag.category == params.category)
    if params.search:
        search_term = f"%{params.search}%"
        query = query.filter(Tag.name.ilike(search_term))

    # Get total count
    total = query.count()

    # Apply pagination and ordering (by weight desc, then name)
    tags = (
        query.order_by(Tag.weight.desc(), Tag.name)
        .offset(params.offset)
        .limit(params.limit)
        .all()
    )

    # Convert to response models and set display_name
    tag_responses = [TagResponse.model_validate(tag) for tag in tags]
    set_tag_list_display_name(tag_responses, user_language)

    return TagListResponse(
        data=tag_responses,
        pagination=PaginationMetadata(
            total=total,
            limit=params.limit,
            offset=params.offset,
            has_next=params.offset + params.limit < total,
            has_prev=params.offset > 0,
        ),
    )


@router.post(
    "", response_model=TagResponse, status_code=status.HTTP_201_CREATED
)
async def create_tag(
    tag_data: TagCreate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:create")),
    user_language: UserLanguage = Depends(get_user_language),
) -> TagResponse:
    """
    Create tag.
    
    Create a new tag for categorization. Tag ID is automatically generated
    as Base64 encoded string from name and category.
    Requires tags:create permission.
    """
    logger.info(f"User {current_user.username} creating tag: {tag_data.name}")

    # Check if tag already exists
    tag_id = Tag.generate_id(tag_data.name, tag_data.category)
    existing_tag = (
        db.query(Tag)
        .filter(
            Tag.id == tag_id,
            Tag.project_id == current_user.project_id,
            Tag.deleted_at.is_(None),
        )
        .first()
    )

    if existing_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tag with this name and category already exists",
        )

    # Create tag
    tag = Tag(
        name=tag_data.name,
        category=tag_data.category,
        project_id=current_user.project_id,
        weight=tag_data.weight,
        color=tag_data.color,
        description=tag_data.description,
        name_zh=tag_data.name_zh,
    )

    db.add(tag)
    db.commit()
    db.refresh(tag)

    logger.info(f"Created tag {tag.id} with name: {tag.name}")

    response = TagResponse.model_validate(tag)
    return set_tag_display_name(response, user_language)


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: str,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:read")),
    user_language: UserLanguage = Depends(get_user_language),
) -> TagResponse:
    """Get tag details. Requires tags:read permission."""
    logger.info(f"User {current_user.username} getting tag: {tag_id}")

    tag = (
        db.query(Tag)
        .filter(
            Tag.id == tag_id,
            Tag.project_id == current_user.project_id,
            Tag.deleted_at.is_(None),
        )
        .first()
    )

    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    response = TagResponse.model_validate(tag)
    return set_tag_display_name(response, user_language)


@router.patch("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: str,
    tag_data: TagUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:update")),
    user_language: UserLanguage = Depends(get_user_language),
) -> TagResponse:
    """
    Update tag.
    
    Update tag properties like weight, color, description, and name_zh.
    Name and category cannot be changed as they determine the tag ID.
    Requires tags:update permission.
    """
    logger.info(f"User {current_user.username} updating tag: {tag_id}")

    tag = (
        db.query(Tag)
        .filter(
            Tag.id == tag_id,
            Tag.project_id == current_user.project_id,
            Tag.deleted_at.is_(None),
        )
        .first()
    )

    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    # Update fields
    update_data = tag_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tag, field, value)

    tag.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(tag)

    logger.info(f"Updated tag {tag.id}")

    response = TagResponse.model_validate(tag)
    return set_tag_display_name(response, user_language)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: str,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:delete")),
) -> None:
    """
    Delete tag (soft delete).
    
    Soft delete a tag. This also removes all visitor-tag associations.
    Requires tags:delete permission.
    """
    logger.info(f"User {current_user.username} deleting tag: {tag_id}")

    tag = (
        db.query(Tag)
        .filter(
            Tag.id == tag_id,
            Tag.project_id == current_user.project_id,
            Tag.deleted_at.is_(None),
        )
        .first()
    )

    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found"
        )

    # Soft delete
    tag.deleted_at = datetime.utcnow()
    tag.updated_at = datetime.utcnow()

    db.commit()

    logger.info(f"Deleted tag {tag.id}")

    return None


@router.post(
    "/visitor-tags",
    response_model=VisitorTagResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_visitor_tag(
    request: Request,
    visitor_tag_data: VisitorTagCreate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:create")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
) -> VisitorTagResponse:
    """Associate a real, currently owned customer with an existing project tag."""
    scope = begin_tag_change(
        db,
        credentials,
        current_user,
        visitor_tag_data.visitor_id,
        visitor_tag_data.tag_id,
    )
    link, output = commit_tag_change(db, scope)
    assert link is not None
    output.check(db)
    request.state.operation_guard = output.guard()
    return VisitorTagResponse.model_validate(link)


@router.delete(
    "/visitor-tags/{visitor_tag_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_visitor_tag(
    request: Request,
    visitor_tag_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:delete")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
) -> None:
    """Remove the original association after checking current customer ownership."""
    scope = begin_tag_removal(db, credentials, current_user, visitor_tag_id)
    _, output = commit_tag_change(db, scope)
    output.check(db)
    request.state.operation_guard = output.guard()


@router.delete(
    "/visitors/{visitor_id}/tags/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_visitor_tag_by_ids(
    request: Request,
    visitor_id: UUID,
    tag_id: str,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("tags:delete")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(original_bearer),
) -> None:
    """Resolve the original association once; do not adopt a later replacement."""
    scope = begin_tag_change(
        db, credentials, current_user, visitor_id, tag_id, remove=True
    )
    _, output = commit_tag_change(db, scope)
    output.check(db)
    request.state.operation_guard = output.guard()

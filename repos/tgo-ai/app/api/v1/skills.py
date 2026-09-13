"""Skill management API endpoints (file-system-based)."""

from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.agent_humanization import humanization_usage

from app.config import settings
from app.schemas.humanization import (
    HumanizationContext, HumanizationMatchRequest, TrainingReview, TrainingPublishRequest,
)
from app.core.logging import get_logger
from app.schemas.skill import (
    HumanizationSkillCreateRequest,
    HumanizationTrainingApplyResponse,
    HumanizationTrainingSampleRequest,
    HumanizationTrainingStatus,
    SkillCreateRequest,
    SkillDetail,
    SkillImportRequest,
    SkillSummary,
    SkillToggleRequest,
    SkillToggleResponse,
    SkillUpdateRequest,
)
from app.services.local_skill_import import import_local_skill
from app.services.skill_file_service import (
    SkillConflictError,
    SkillFileService,
    SkillNotFoundError,
    SkillPathTraversalError,
    SkillReadOnlyError,
)

logger = get_logger(__name__)

router = APIRouter()


def _get_skill_service() -> SkillFileService:
    """Get a SkillFileService instance using the configured base directory."""
    return SkillFileService(settings.skills_base_dir)


def _get_project_id(x_project_id: str = Header(..., description="Project ID")) -> str:
    """Extract project_id from the X-Project-Id header."""
    return x_project_id


# ---------------------------------------------------------------------------
# Skill CRUD endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=List[SkillSummary],
    summary="List all skills",
    description="List all skills visible to the project (private + official).",
)
async def list_skills(
    x_project_id: str = Header(..., alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
) -> List[SkillSummary]:
    service = _get_skill_service()
    skills = await service.list_skills(x_project_id)
    usage = await humanization_usage(db, x_project_id)
    for skill in skills:
        skill.used_by = usage.get(skill.name, [])
    return skills


@router.post(
    "",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new skill",
    description="Create a new project-private skill directory with SKILL.md.",
)
async def create_skill(
    data: SkillCreateRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    service = _get_skill_service()
    try:
        return await service.create_skill(x_project_id, data)
    except SkillConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.post(
    "/humanization",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a humanization skill",
    description="Create a manually trained customer-service humanization skill.",
)
async def create_humanization_skill(
    data: HumanizationSkillCreateRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    service = _get_skill_service()
    try:
        return await service.create_humanization_skill(x_project_id, data)
    except SkillConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.post(
    "/{skill_name}/training-samples",
    response_model=HumanizationTrainingStatus,
    summary="Add a pending humanization correction",
    description="Store an assist-mode edit without changing the published skill.",
)
async def add_humanization_training_sample(
    skill_name: str,
    data: HumanizationTrainingSampleRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> HumanizationTrainingStatus:
    service = _get_skill_service()
    try:
        return await service.add_humanization_training_sample(
            x_project_id, skill_name, data
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.post(
    "/{skill_name}/apply-training",
    response_model=HumanizationTrainingApplyResponse,
    summary="Publish pending humanization corrections",
    description="Apply pending samples only after an explicit administrator action.",
)
async def apply_humanization_training(
    skill_name: str,
    data: TrainingPublishRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> HumanizationTrainingApplyResponse:
    service = _get_skill_service()
    try:
        return await service.publish_humanization_training(
            x_project_id, skill_name, data
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.get("/{skill_name}/training-review", response_model=TrainingReview)
async def review_training(skill_name: str, x_project_id: str = Header(..., alias="X-Project-Id")) -> TrainingReview:
    try:
        return await _get_skill_service().review_humanization_training(x_project_id, skill_name)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{skill_name}/match-context", response_model=HumanizationContext)
async def match_context(skill_name: str, data: HumanizationMatchRequest,
                        x_project_id: str = Header(..., alias="X-Project-Id")) -> HumanizationContext:
    try:
        return await _get_skill_service().match_humanization_context(
            x_project_id, skill_name, data.customer_message, data.candidate,
            data.factual_draft, data.recent_messages)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/{skill_name}",
    response_model=SkillDetail,
    summary="Get skill details",
    description="Get the full detail of a skill including instructions and file listings.",
)
async def get_skill(
    skill_name: str,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    service = _get_skill_service()
    try:
        return await service.get_skill(x_project_id, skill_name)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.patch(
    "/{skill_name}",
    response_model=SkillDetail,
    summary="Update a skill",
    description="Update SKILL.md content for a project-private skill.",
)
async def update_skill(
    skill_name: str,
    data: SkillUpdateRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    service = _get_skill_service()
    try:
        return await service.update_skill(x_project_id, skill_name, data)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.delete(
    "/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill",
    description="Delete a project-private skill directory entirely.",
)
async def delete_skill(
    skill_name: str,
    x_project_id: str = Header(..., alias="X-Project-Id"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    service = _get_skill_service()
    if (await humanization_usage(db, x_project_id)).get(skill_name):
        raise HTTPException(409, "这个拟人技能仍绑定着AI员工，请先在员工设置中解除绑定，再删除")
    try:
        await service.delete_skill(x_project_id, skill_name)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


# ---------------------------------------------------------------------------
# Skill toggle (enable / disable)
# ---------------------------------------------------------------------------


@router.put(
    "/{skill_name}/toggle",
    response_model=SkillToggleResponse,
    summary="Toggle skill enabled/disabled",
    description="Enable or disable a skill for the current project.",
)
async def toggle_skill(
    skill_name: str,
    data: SkillToggleRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillToggleResponse:
    service = _get_skill_service()
    try:
        new_state = await service.toggle_skill(x_project_id, skill_name, data.enabled)
        return SkillToggleResponse(name=skill_name, enabled=new_state)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


# ---------------------------------------------------------------------------
# Local skill import
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Import a local skill",
)
async def import_skill(
    data: SkillImportRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    try:
        return await import_local_skill(_get_skill_service(), x_project_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SkillConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

# ---------------------------------------------------------------------------
# Skill sub-file endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{skill_name}/files/{file_path:path}",
    summary="Read a skill sub-file",
    description="Read the content of a script or reference file within a skill.",
)
async def get_skill_file(
    skill_name: str,
    file_path: str,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> Response:
    service = _get_skill_service()
    try:
        content = await service.get_file(x_project_id, skill_name, file_path)
        return Response(content=content, media_type="text/plain; charset=utf-8")
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillPathTraversalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


@router.put(
    "/{skill_name}/files/{file_path:path}",
    status_code=status.HTTP_200_OK,
    summary="Create or update a skill sub-file",
    description="Create or update a script or reference file within a project-private skill.",
)
async def put_skill_file(
    skill_name: str,
    file_path: str,
    content: str,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> dict:
    service = _get_skill_service()
    try:
        await service.put_file(x_project_id, skill_name, file_path, content)
        return {"status": "ok", "file_path": file_path}
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except SkillPathTraversalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.delete(
    "/{skill_name}/files/{file_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill sub-file",
    description="Delete a script or reference file from a project-private skill.",
)
async def delete_skill_file(
    skill_name: str,
    file_path: str,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> Response:
    service = _get_skill_service()
    try:
        await service.delete_file(x_project_id, skill_name, file_path)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except SkillPathTraversalError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

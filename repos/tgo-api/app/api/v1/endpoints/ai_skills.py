"""AI Skills proxy endpoints (forwarded to tgo-ai service)."""

import base64
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Response, UploadFile, status

from app.core.logging import get_logger
from app.core.security import get_authenticated_project
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
from app.services.ai_client import ai_client
from app.schemas.humanization import (
    TrainingReview, TrainingPublishRequest, HumanizationTryRequest, HumanizationTryResponse,
)
from app.services.humanization_review_service import analyze_training, try_humanization

logger = get_logger("endpoints.ai_skills")
router = APIRouter()


# ---------------------------------------------------------------------------
# Skill CRUD endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=List[SkillSummary],
    summary="List all skills",
    description="List all skills visible to the current project (private + official).",
)
async def list_skills(
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> List[SkillSummary]:
    project, _ = auth_data
    project_id = str(project.id)
    result = await ai_client.list_skills(project_id)
    return [SkillSummary(**item) for item in result]


@router.post(
    "",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new skill",
    description="Create a new project-private skill.",
)
async def create_skill(
    data: SkillCreateRequest,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillDetail:
    project, _ = auth_data
    project_id = str(project.id)
    result = await ai_client.create_skill(project_id, data.model_dump(exclude_none=True))
    return SkillDetail(**result)


@router.post(
    "/humanization",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a humanization skill",
)
async def create_humanization_skill(
    data: HumanizationSkillCreateRequest,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillDetail:
    project, _ = auth_data
    result = await ai_client.create_humanization_skill(
        str(project.id), data.model_dump(exclude_none=True)
    )
    return SkillDetail(**result)


@router.post(
    "/{skill_name}/training-samples",
    response_model=HumanizationTrainingStatus,
    summary="Add a pending humanization correction",
)
async def add_humanization_training_sample(
    skill_name: str,
    data: HumanizationTrainingSampleRequest,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> HumanizationTrainingStatus:
    project, _ = auth_data
    result = await ai_client.add_humanization_training_sample(
        str(project.id), skill_name, data.model_dump(exclude_none=True)
    )
    return HumanizationTrainingStatus(**result)


@router.post(
    "/{skill_name}/apply-training",
    response_model=HumanizationTrainingApplyResponse,
    summary="Publish pending humanization corrections",
)
async def apply_humanization_training(
    skill_name: str,
    data: TrainingPublishRequest,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> HumanizationTrainingApplyResponse:
    project, _ = auth_data
    result = await ai_client.apply_humanization_training(
        str(project.id), skill_name, data.model_dump()
    )
    return HumanizationTrainingApplyResponse(**result)


@router.get("/{skill_name}/training-review", response_model=TrainingReview)
async def review_training(skill_name: str,
                          auth_data: tuple[Any, str] = Depends(get_authenticated_project)) -> TrainingReview:
    return await ai_client.review_humanization_training(str(auth_data[0].id), skill_name)


@router.post("/{skill_name}/training-preview", response_model=TrainingReview)
async def preview_training(skill_name: str,
                           auth_data: tuple[Any, str] = Depends(get_authenticated_project)) -> TrainingReview:
    return await analyze_training(str(auth_data[0].id), skill_name)


@router.post("/{skill_name}/try-reply", response_model=HumanizationTryResponse)
async def try_reply(skill_name: str, data: HumanizationTryRequest,
                    auth_data: tuple[Any, str] = Depends(get_authenticated_project)) -> HumanizationTryResponse:
    try:
        return await try_humanization(str(auth_data[0].id), skill_name, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get(
    "/{skill_name}",
    response_model=SkillDetail,
    summary="Get skill details",
    description="Get full detail of a skill including instructions and file listings.",
)
async def get_skill(
    skill_name: str,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillDetail:
    project, _ = auth_data
    project_id = str(project.id)
    result = await ai_client.get_skill(project_id, skill_name)
    return SkillDetail(**result)


@router.patch(
    "/{skill_name}",
    response_model=SkillDetail,
    summary="Update a skill",
    description="Update SKILL.md content for a project-private skill.",
)
async def update_skill(
    skill_name: str,
    data: SkillUpdateRequest,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillDetail:
    project, _ = auth_data
    project_id = str(project.id)
    result = await ai_client.update_skill(
        project_id, skill_name, data.model_dump(exclude_none=True)
    )
    return SkillDetail(**result)


@router.delete(
    "/{skill_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill",
    description="Delete a project-private skill directory entirely.",
)
async def delete_skill(
    skill_name: str,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> Response:
    project, _ = auth_data
    project_id = str(project.id)
    await ai_client.delete_skill(project_id, skill_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillToggleResponse:
    project, _ = auth_data
    project_id = str(project.id)
    result = await ai_client.toggle_skill(
        project_id, skill_name, data.model_dump()
    )
    return SkillToggleResponse(**result)


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
    file: UploadFile = File(...),
    display_name: str = Form(default="", max_length=100),
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> SkillDetail:
    project, _ = auth_data
    try:
        content = await file.read(10 * 1024 * 1024 + 1)
    finally:
        await file.close()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="上传文件不能超过 10 MB")
    if not content:
        raise HTTPException(status_code=422, detail="上传文件不能为空")
    filename = file.filename or "SKILL.md"
    if len(filename) > 255 or not (filename.lower() == "skill.md" or filename.lower().endswith(".zip")):
        raise HTTPException(status_code=422, detail="请选择 SKILL.md 或 ZIP 技能包")
    data = SkillImportRequest(
        filename=filename,
        content_base64=base64.b64encode(content).decode("ascii"),
        display_name=display_name.strip() or None,
    )
    result = await ai_client.import_skill(str(project.id), data.model_dump(exclude_none=True))
    return SkillDetail(**result)

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
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> Response:
    project, _ = auth_data
    project_id = str(project.id)
    content = await ai_client.get_skill_file(project_id, skill_name, file_path)
    return Response(content=content, media_type="text/plain; charset=utf-8")


@router.put(
    "/{skill_name}/files/{file_path:path}",
    status_code=status.HTTP_200_OK,
    summary="Create or update a skill sub-file",
    description="Create or update a script or reference file within a project-private skill.",
)
async def put_skill_file(
    skill_name: str,
    file_path: str,
    content: str = Body(..., media_type="text/plain"),
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> Dict[str, str]:
    project, _ = auth_data
    project_id = str(project.id)
    await ai_client.put_skill_file(project_id, skill_name, file_path, content)
    return {"status": "ok", "file_path": file_path}


@router.delete(
    "/{skill_name}/files/{file_path:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a skill sub-file",
    description="Delete a script or reference file from a project-private skill.",
)
async def delete_skill_file(
    skill_name: str,
    file_path: str,
    auth_data: tuple[Any, str] = Depends(get_authenticated_project),
) -> Response:
    project, _ = auth_data
    project_id = str(project.id)
    await ai_client.delete_skill_file(project_id, skill_name, file_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

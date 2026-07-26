"""Skill management API endpoints (file-system-based)."""

from typing import List

from fastapi import APIRouter, Header, HTTPException, Response, status

from app.config import settings
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
from app.services.github_skill_downloader import (
    GitHubDownloadError,
    GitHubSkillDownloader,
    GitHubURLParseError,
    SkillValidationError,
)
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
) -> List[SkillSummary]:
    service = _get_skill_service()
    return await service.list_skills(x_project_id)


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
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> HumanizationTrainingApplyResponse:
    service = _get_skill_service()
    try:
        return await service.apply_humanization_training(
            x_project_id, skill_name
        )
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SkillReadOnlyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )


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
) -> Response:
    service = _get_skill_service()
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
# Skill import from GitHub
# ---------------------------------------------------------------------------


@router.post(
    "/import",
    response_model=SkillDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Import a skill from GitHub",
    description="Download a skill directory from a GitHub URL and create it as a project-private skill.",
)
async def import_skill_from_github(
    data: SkillImportRequest,
    x_project_id: str = Header(..., alias="X-Project-Id"),
) -> SkillDetail:
    service = _get_skill_service()
    downloader = GitHubSkillDownloader()

    # Use provided token or fall back to server-configured default
    token = data.github_token or settings.github_token

    try:
        import shutil
        from pathlib import Path as _Path

        # Parse URL to extract owner/repo/ref/path and derive skill name
        _owner, _repo, _ref, path = GitHubSkillDownloader.parse_github_url(data.github_url)
        url_skill_name = path.rstrip("/").split("/")[-1]

        project_dir = _Path(settings.skills_base_dir) / x_project_id

        # Download to a temporary staging directory (not the final location)
        staging_dir = project_dir / f".importing-{url_skill_name}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

        skill_name = await downloader.download_skill(
            github_url=data.github_url,
            target_dir=staging_dir,
            github_token=token,
        )

        # Check for conflicts with the validated name
        final_dir = project_dir / skill_name
        if final_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise SkillConflictError(f"Skill '{skill_name}' already exists")

        # Move staging -> final
        staging_dir.rename(final_dir)

        # Return the full detail
        return service._parse_skill_detail(final_dir)

    except GitHubURLParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except GitHubDownloadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        )
    except SkillValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except SkillConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        )


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

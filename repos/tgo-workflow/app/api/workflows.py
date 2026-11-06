from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.workflow_service import WorkflowService
from app.services.validation_service import ValidationService
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowUpdate,
    WorkflowInDB,
    WorkflowSummary,
    WorkflowValidationResponse,
    WorkflowValidateRequest,
    WorkflowDuplicateRequest,
    WorkflowVariablesResponse,
    WorkflowBatchInfo,
    WorkflowIntegrationRelease,
)
from app.schemas.common import PaginatedResponse, PaginationInfo
from typing import List, Optional
from app.schemas.capabilities import WorkflowCapabilities
from app.services.node_readiness import workflow_capabilities
from app.services import workflow_releases
from app.services.workflow_definition_inspection import (
    WorkflowDefinitionReadError,
    available_variables,
    input_parameters,
)
from app.schemas.workflow_release import (
    WorkflowPublishRequest,
    WorkflowReleaseSummary,
    WorkflowRollbackRequest,
)
from app.schemas.device_references import DeviceWorkflowReferences
from app.services.device_workflow_references import (
    list_device_workflow_references,
)
from uuid import UUID
from pydantic import JsonValue, ValidationError

router = APIRouter()


@router.get("/capabilities", response_model=WorkflowCapabilities)
async def get_workflow_capabilities() -> WorkflowCapabilities:
    return workflow_capabilities()


@router.get("/device-references", response_model=DeviceWorkflowReferences)
async def get_device_workflow_references(
    project_id: str = Query(...),
    device_id: UUID = Query(...),
    limit: int = Query(5, ge=1, le=20),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> DeviceWorkflowReferences:
    return await list_device_workflow_references(
        db,
        project_id,
        device_id,
        limit=limit,
        offset=offset,
    )


@router.get("/", response_model=PaginatedResponse[WorkflowSummary])
async def get_workflows(
    project_id: str = Query(..., description="Project ID"),
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    search: Optional[str] = None,
    tags: Optional[List[str]] = Query(None),
    sort_by: str = "updated_at",
    sort_order: str = "desc",
    db: AsyncSession = Depends(get_db),
):
    workflows, total = await WorkflowService.get_all(
        db,
        project_id=project_id,
        skip=skip,
        limit=limit,
        status=status,
        search=search,
        tags=tags,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    return PaginatedResponse(
        data=workflows,
        pagination=PaginationInfo(
            total=total,
            limit=limit,
            offset=skip,
            has_next=total > skip + limit,
            has_prev=skip > 0,
        ),
    )


@router.post(
    "/", response_model=WorkflowInDB, status_code=status.HTTP_201_CREATED
)
async def create_workflow(
    workflow_in: WorkflowCreate,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    return await WorkflowService.create(db, project_id, workflow_in)


@router.get("/batch", response_model=List[WorkflowBatchInfo])
async def get_workflows_batch(
    workflow_ids: List[str] = Query(..., description="List of workflow IDs"),
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get basic information for multiple workflows by their IDs,
    including input parameters.
    This is useful for agents to understand what parameters are required
    to execute each workflow.
    """
    workflows = await WorkflowService.get_by_ids(db, project_id, workflow_ids)

    result = []
    for workflow in workflows:
        definition = workflow.definition
        if workflow.status == "active":
            published = await workflow_releases.production_definition(
                db, workflow.id, project_id
            )
            definition = published.definition
        try:
            parameters = input_parameters(definition, batch_defaults=True)
            result.append(
                WorkflowBatchInfo.model_validate(
                    {
                        "id": workflow.id,
                        "project_id": workflow.project_id,
                        "name": workflow.name,
                        "description": workflow.description,
                        "tags": workflow.tags,
                        "status": workflow.status,
                        "version": workflow.version,
                        "published_version": workflow.published_version,
                        "publication_revision": workflow.publication_revision,
                        "updated_at": workflow.updated_at,
                        "input_parameters": parameters,
                    }
                )
            )
        except (WorkflowDefinitionReadError, ValidationError):
            raise HTTPException(409, "工作流配置信息无法核验，请检查并重新保存或发布。") from None

    return result


@router.get("/{workflow_id}", response_model=WorkflowInDB)
async def get_workflow(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.put("/{workflow_id}", response_model=WorkflowInDB)
async def update_workflow(
    workflow_id: str,
    workflow_in: WorkflowUpdate,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    if workflow_in.status == "active":
        raise HTTPException(409, "请使用发布入口启用工作流，不能直接修改为已发布。")
    existing = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow = await WorkflowService.update(
        db, workflow_id, project_id, workflow_in
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    success = await WorkflowService.delete(db, workflow_id, project_id)
    if not success:
        raise HTTPException(status_code=404, detail="Workflow not found")


@router.post("/{workflow_id}/duplicate", response_model=WorkflowInDB)
async def duplicate_workflow(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    request: Optional[WorkflowDuplicateRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    new_name = request.name if request else None
    workflow = await WorkflowService.duplicate(
        db, workflow_id, project_id, new_name
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow


@router.post("/validate", response_model=WorkflowValidationResponse)
async def validate_workflow_generic(
    request: WorkflowValidateRequest,
    project_id: str = Query(..., description="Project ID"),
):
    definition: dict[str, JsonValue] = {
        "nodes": [node.model_dump() for node in request.nodes],
        "edges": [edge.model_dump() for edge in request.edges],
    }
    errors = ValidationService.validate_workflow(definition)
    return WorkflowValidationResponse(valid=len(errors) == 0, errors=errors)


@router.post(
    "/{workflow_id}/validate", response_model=WorkflowValidationResponse
)
async def validate_workflow(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    errors = ValidationService.validate_workflow(workflow.definition)
    return WorkflowValidationResponse(valid=len(errors) == 0, errors=errors)


@router.post("/{workflow_id}/publish", response_model=WorkflowInDB)
async def publish_workflow(
    workflow_id: str,
    request: WorkflowPublishRequest,
    project_id: str = Query(..., description="Project ID"),
    staff_id: str | None = Query(None, description="Publishing staff ID"),
    db: AsyncSession = Depends(get_db),
):
    workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    # Validate before publishing
    errors = ValidationService.validate_workflow(workflow.definition)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Workflow validation failed", "errors": errors},
        )

    updated_workflow = await WorkflowService.publish(
        db, workflow_id, project_id, request, staff_id
    )
    return updated_workflow


@router.get(
    "/{workflow_id}/releases", response_model=list[WorkflowReleaseSummary]
)
async def get_workflow_releases(
    workflow_id: str,
    project_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[WorkflowReleaseSummary]:
    return await workflow_releases.history(db, workflow_id, project_id)


@router.get(
    "/{workflow_id}/releases/{version}/integration",
    response_model=WorkflowIntegrationRelease,
)
async def get_workflow_integration_release(
    workflow_id: str,
    version: int,
    project_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> WorkflowIntegrationRelease:
    return await workflow_releases.integration_release(
        db, workflow_id, project_id, version
    )


@router.post("/{workflow_id}/rollback", response_model=WorkflowInDB)
async def rollback_workflow(
    workflow_id: str,
    request: WorkflowRollbackRequest,
    project_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_releases.rollback(
        db, workflow_id, project_id, request
    )


@router.get(
    "/{workflow_id}/variables", response_model=WorkflowVariablesResponse
)
async def get_workflow_variables(
    workflow_id: str,
    project_id: str = Query(..., description="Project ID"),
    db: AsyncSession = Depends(get_db),
):
    workflow = await WorkflowService.get_by_id(db, workflow_id, project_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    try:
        variables = available_variables(workflow.definition)
    except WorkflowDefinitionReadError:
        raise HTTPException(409, "工作流变量无法核验，请检查并重新保存。") from None
    return WorkflowVariablesResponse(variables=variables)

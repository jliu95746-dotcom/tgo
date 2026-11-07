"""Workflow Service client for external workflow data retrieval."""

from typing import Dict, List, Literal, Optional
from uuid import UUID

import httpx
from pydantic import BaseModel, Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from app.config import settings
from app.exceptions import NotFoundError, ValidationError


class WorkflowData(BaseModel):
    """Workflow data from Workflow service."""

    id: str = Field(description="Workflow ID")
    name: str = Field(description="Workflow name")
    description: Optional[str] = Field(default=None, description="Workflow description")
    tags: List[str] = Field(default_factory=list, description="Workflow tags")
    status: str = Field(description="Current status")
    version: int = Field(description="Version number")
    published_version: Optional[int] = None
    enabled: bool = True
    bound_release_version: Optional[int] = None
    bound_release_id: Optional[str] = None
    bound_release_digest: Optional[str] = None
    details_available: bool = True
    updated_at: Optional[str] = Field(description="Last update time, null if details are unavailable")


class WorkflowInputParameter(BaseModel):
    name: str
    type: str = "string"
    description: Optional[str] = None
    required: bool = False


class WorkflowBusinessQueryDependency(BaseModel):
    connection_id: UUID
    operation: Literal["order_query", "logistics_query"]


class WorkflowIntegrationRelease(BaseModel):
    """Verified fixed publication contract returned by tgo-workflow."""

    workflow_id: str
    release_version: int = Field(ge=1)
    release_id: str
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_parameters: List[WorkflowInputParameter] = Field(default_factory=list)
    node_types: Optional[List[str]] = None
    required_business_queries: Optional[List[WorkflowBusinessQueryDependency]] = None
    approval_actions: Optional[List[str]] = None


class WorkflowBatchResponse(BaseModel):
    """Response model for batch workflow retrieval."""

    workflows: List[WorkflowData] = Field(description="Found workflows")
    not_found: List[str] = Field(description="Workflow IDs that were not found")


_workflow_batch = TypeAdapter(List[WorkflowData])


class WorkflowServiceClient:
    """Client for interacting with the external Workflow service."""

    def __init__(self) -> None:
        """Initialize the Workflow service client."""
        self.base_url = settings.workflow_service_url
        self.timeout = 30.0

    async def get_workflows_batch(
        self,
        workflow_ids: List[str],
        project_id: str
    ) -> List[WorkflowData]:
        """
        Retrieve multiple workflows by their IDs from the Workflow service.

        Args:
            workflow_ids: List of workflow ID strings
            project_id: Project ID

        Returns:
            List of WorkflowData
        """
        if not workflow_ids:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/v1/workflows/batch",
                    params={
                        "project_id": project_id,
                        "workflow_ids": workflow_ids
                    },
                )

                if response.status_code == 200:
                    result = _workflow_batch.validate_python(response.json())
                    found = [row.id for row in result]
                    if (
                        len(found) != len(set(found))
                        or not set(found).issubset(workflow_ids)
                    ):
                        raise ValueError("Unexpected workflow batch IDs")
                    return result
                if response.status_code == 422:
                    message = "Invalid workflow IDs"
                else:
                    message = (
                        f"Workflow service error: {response.status_code}"
                    )
                raise ValidationError(
                    message, "workflows",
                    {"status_code": response.status_code},
                )
        except httpx.RequestError:
            raise NotFoundError("Workflow Service") from None
        except ValueError:
            raise ValidationError(
                "Invalid workflow batch response", "workflows"
            ) from None

    async def resolve_published_bindings(
        self, workflow_ids: List[str], project_id: str,
        selected_versions: Optional[Dict[str, int]] = None,
    ) -> Dict[str, WorkflowIntegrationRelease]:
        """Pin the active release and digest when an employee is saved."""
        selected_versions = selected_versions or {}
        if not set(selected_versions).issubset(set(workflow_ids)):
            raise ValidationError("指定版本的工作流必须已关联员工", "workflow_release_versions")
        if any(type(version) is not int or version < 1
               for version in selected_versions.values()):
            raise ValidationError("工作流发布版本必须是正整数", "workflow_release_versions")
        if not workflow_ids:
            return {}
        workflows = await self.get_workflows_batch(workflow_ids, project_id)
        found = {workflow.id for workflow in workflows}
        if found != set(workflow_ids):
            raise NotFoundError("Workflow")
        resolved: Dict[str, WorkflowIntegrationRelease] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for workflow in workflows:
                current_version = workflow.published_version
                if (workflow.status != "active" or current_version is None
                        or current_version < 1):
                    raise ValidationError(
                        "请先发布所选工作流，再关联为员工业务能力。", "workflows"
                    )
                version = selected_versions.get(workflow.id, current_version)
                try:
                    response = await client.get(
                        f"{self.base_url}/v1/workflows/{workflow.id}/releases/"
                        f"{version}/integration",
                        params={"project_id": project_id},
                    )
                    response.raise_for_status()
                    release = WorkflowIntegrationRelease.model_validate(response.json())
                except (httpx.RequestError, httpx.HTTPStatusError, PydanticValidationError, ValueError) as exc:
                    raise ValidationError(
                        "工作流发布版本无法核验，请稍后重试。", "workflows"
                    ) from exc
                if (
                    release.workflow_id != workflow.id
                    or release.release_version != version
                ):
                    raise ValidationError(
                        "工作流发布版本与当前项目不一致。", "workflows"
                    )
                resolved[workflow.id] = release
        return resolved

    async def validate_workflows_exist(
        self,
        workflow_ids: List[str],
        project_id: str
    ) -> None:
        """
        Validate that workflows exist in the Workflow service.

        Args:
            workflow_ids: List of workflow ID strings to validate
            project_id: Project ID

        Raises:
            NotFoundError: If any workflow is not found
            ValidationError: If validation fails
        """
        if not workflow_ids:
            return

        workflows = await self.get_workflows_batch(workflow_ids, project_id)
        found_ids = {w.id for w in workflows}
        missing_ids = set(workflow_ids) - found_ids

        if missing_ids:
            raise NotFoundError(
                "Workflow",
                details={"missing_ids": sorted(missing_ids)},
            )


# Global Workflow service client instance
workflow_service_client = WorkflowServiceClient()

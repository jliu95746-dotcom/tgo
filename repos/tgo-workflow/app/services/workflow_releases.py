"""Publish and recover fixed graphs with optimistic concurrency checks."""

from dataclasses import dataclass
import hmac

from fastapi import HTTPException
from pydantic import JsonValue, ValidationError as PydanticValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.workflow_release import (
    WorkflowPublishRequest,
    WorkflowReleaseSummary,
    WorkflowRollbackRequest,
)
from app.services.execution_snapshot import copy_json_object, definition_digest
from app.services.validation_service import ValidationService
from app.services.workflow_definition_inspection import (
    WorkflowDefinitionReadError,
    definition_nodes,
    input_parameters,
)
from app.schemas.workflow import (
    WorkflowBusinessQueryDependency,
    WorkflowInputParameter,
    WorkflowIntegrationRelease,
)
from app.integrations.ai_client import AIClient
from app.integrations.api_client import verify_definition_approval_assignees

CONFLICT = "流程已有新的修改或发布，请重新读取后再操作。"


async def _workflow(
    db: AsyncSession, workflow_id: str, project_id: str
) -> Workflow:
    workflow = (
        await db.scalars(
            select(Workflow)
            .where(
                Workflow.id == workflow_id,
                Workflow.project_id == project_id,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if workflow is None:
        raise HTTPException(404, "Workflow not found")
    return workflow


def _validated(
    definition: dict[str, JsonValue],
    *,
    allow_webhook_preparation: bool = False,
) -> dict[str, JsonValue]:
    owned = copy_json_object(definition)
    errors = ValidationService.validate_workflow(
        owned,
        allow_webhook_preparation=allow_webhook_preparation,
    )
    if errors:
        raise HTTPException(
            400, {"message": "Workflow validation failed", "errors": errors}
        )
    return owned


async def verify_tool_dependencies(
    definition: dict[str, JsonValue], project_id: str
) -> None:
    """Verify tools before publication or production execution."""
    tool_ids: set[str] = set()
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "tool":
            continue
        data = node.get("data")
        if isinstance(data, dict) and data.get("tool_id"):
            tool_ids.add(str(data["tool_id"]))
    for tool_id in sorted(tool_ids):
        try:
            await AIClient.verify_tool_available(project_id, tool_id)
        except PermissionError:
            raise HTTPException(409, "工作流引用的业务工具不存在或已撤权") from None
        except RuntimeError:
            raise HTTPException(503, "业务工具依赖暂时无法核验") from None


async def verify_approval_dependencies(
    definition: dict[str, JsonValue], project_id: str
) -> None:
    """An approval can target only a currently enabled human in this tenant."""
    try:
        await verify_definition_approval_assignees(definition, project_id)
    except PermissionError:
        raise HTTPException(409, "审批员工已撤权或不属于当前企业") from None
    except RuntimeError:
        raise HTTPException(503, "审批员工归属暂时无法核验") from None


async def publish(
    db: AsyncSession,
    workflow_id: str,
    project_id: str,
    request: WorkflowPublishRequest,
    staff_id: str | None,
) -> Workflow:
    workflow = await _workflow(db, workflow_id, project_id)
    if (
        workflow.version != request.expected_draft_version
        or workflow.publication_revision
        != request.expected_publication_revision
    ):
        raise HTTPException(409, CONFLICT)
    definition = _validated(
        workflow.definition, allow_webhook_preparation=True
    )
    await verify_tool_dependencies(definition, project_id)
    await verify_approval_dependencies(definition, project_id)
    previous = await db.scalar(
        select(func.max(WorkflowRelease.version)).where(
            WorkflowRelease.workflow_id == workflow_id,
            WorkflowRelease.project_id == project_id,
        )
    )
    version = (previous or 0) + 1
    changed = await db.scalar(
        update(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.project_id == project_id,
            Workflow.version == request.expected_draft_version,
            Workflow.publication_revision
            == request.expected_publication_revision,
        )
        .values(
            published_version=version,
            status="active",
            publication_revision=Workflow.publication_revision + 1,
        )
        .returning(Workflow.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        raise HTTPException(409, CONFLICT)
    db.add(
        WorkflowRelease(
            workflow_id=workflow_id,
            project_id=project_id,
            version=version,
            draft_version=request.expected_draft_version,
            definition=definition,
            definition_digest=definition_digest(definition),
            created_by=staff_id,
        )
    )
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, CONFLICT) from None
    return await _workflow(db, workflow_id, project_id)


async def history(
    db: AsyncSession, workflow_id: str, project_id: str
) -> list[WorkflowReleaseSummary]:
    await _workflow(db, workflow_id, project_id)
    rows = (
        await db.scalars(
            select(WorkflowRelease)
            .where(
                WorkflowRelease.workflow_id == workflow_id,
                WorkflowRelease.project_id == project_id,
            )
            .order_by(WorkflowRelease.version.desc())
            .limit(100)
        )
    ).all()
    return [WorkflowReleaseSummary.model_validate(row) for row in rows]


async def _release(
    db: AsyncSession, workflow_id: str, project_id: str, version: int
) -> WorkflowRelease:
    row = (
        await db.scalars(
            select(WorkflowRelease)
            .where(
                WorkflowRelease.workflow_id == workflow_id,
                WorkflowRelease.project_id == project_id,
                WorkflowRelease.version == version,
            )
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "此发布版本不存在。")
    try:
        owned = copy_json_object(row.definition)
        if not hmac.compare_digest(
            definition_digest(owned), row.definition_digest
        ):
            raise ValueError("Invalid release digest")
    except (TypeError, ValueError):
        raise HTTPException(409, "发布版本校验失败，请重新发布有效流程。") from None
    return row


async def rollback(
    db: AsyncSession,
    workflow_id: str,
    project_id: str,
    request: WorkflowRollbackRequest,
) -> Workflow:
    await _workflow(db, workflow_id, project_id)
    row = await _release(db, workflow_id, project_id, request.target_version)
    _validated(row.definition, allow_webhook_preparation=True)
    await verify_tool_dependencies(row.definition, project_id)
    await verify_approval_dependencies(row.definition, project_id)
    changed = await db.scalar(
        update(Workflow)
        .where(
            Workflow.id == workflow_id,
            Workflow.project_id == project_id,
            Workflow.publication_revision
            == request.expected_publication_revision,
        )
        .values(
            published_version=row.version,
            status="active",
            publication_revision=Workflow.publication_revision + 1,
        )
        .returning(Workflow.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        await db.rollback()
        raise HTTPException(409, CONFLICT)
    await db.flush()
    return await _workflow(db, workflow_id, project_id)


@dataclass(frozen=True)
class PublishedDefinition:
    definition: dict[str, JsonValue]
    version: int
    release_id: str


def verify_execution_binding(
    published: PublishedDefinition,
    expected_release_id: str | None,
    expected_release_digest: str | None,
) -> None:
    """Reject a changed release before creating an execution record."""
    if expected_release_id is None and expected_release_digest is None:
        return
    if expected_release_id is None or expected_release_digest is None:
        raise HTTPException(422, "发布记录 ID 和摘要必须同时提供。")
    if published.release_id != expected_release_id or not hmac.compare_digest(
        definition_digest(published.definition), expected_release_digest
    ):
        raise HTTPException(409, "工作流固定发布版本已变更，请重新关联。")


async def production_definition(
    db: AsyncSession,
    workflow_id: str,
    project_id: str,
    fixed_version: int | None = None,
) -> PublishedDefinition:
    workflow = await _workflow(db, workflow_id, project_id)
    if workflow.status != "active" or workflow.published_version is None:
        raise HTTPException(409, "请先发布工作流，再用于正式业务执行。")
    row = await _release(
        db,
        workflow_id,
        project_id,
        fixed_version or workflow.published_version,
    )
    await verify_tool_dependencies(row.definition, project_id)
    return PublishedDefinition(
        copy_json_object(row.definition), row.version, row.id
    )


async def integration_release(
    db: AsyncSession, workflow_id: str, project_id: str, version: int
) -> WorkflowIntegrationRelease:
    """Describe exactly the release an employee binding will execute."""
    published = await production_definition(
        db, workflow_id, project_id, version
    )
    try:
        nodes = definition_nodes(published.definition)
        parameters: list[WorkflowInputParameter] = input_parameters(
            published.definition
        )
    except WorkflowDefinitionReadError:
        raise HTTPException(409, "工作流发布定义无法核验，请重新发布。") from None
    node_types: list[str] = []
    business_queries: list[WorkflowBusinessQueryDependency] = []
    approval_actions: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise HTTPException(409, "工作流发布定义无法核验，请重新发布。")
        kind = node.get("type")
        if not isinstance(kind, str):
            raise HTTPException(409, "工作流发布节点无法核验，请重新发布。")
        node_types.append(kind)
        if kind == "approval":
            data = node.get("data")
            if not isinstance(data, dict):
                raise HTTPException(409, "工作流审批依赖无法核验，请重新发布。")
            approval_actions.append(
                str(data.get("after_approval_action") or "")
            )
        if kind == "business_query":
            data = node.get("data")
            if not isinstance(data, dict):
                raise HTTPException(409, "工作流业务查询依赖无法核验，请重新发布。")
            try:
                business_queries.append(
                    WorkflowBusinessQueryDependency.model_validate(
                        {
                            "connection_id": data.get("connection_id"),
                            "operation": data.get("operation"),
                        }
                    )
                )
            except PydanticValidationError as exc:
                raise HTTPException(409, "工作流业务查询依赖无法核验，请重新发布。") from exc
    if approval_actions:
        await verify_approval_dependencies(published.definition, project_id)
    return WorkflowIntegrationRelease(
        workflow_id=workflow_id,
        release_version=published.version,
        release_id=published.release_id,
        definition_digest=definition_digest(published.definition),
        input_parameters=parameters,
        node_types=node_types,
        required_business_queries=business_queries,
        approval_actions=approval_actions,
    )

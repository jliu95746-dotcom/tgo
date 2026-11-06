"""Find workflows whose draft or live release pins a given device."""

from collections.abc import Mapping
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import Workflow
from app.models.workflow_release import WorkflowRelease
from app.schemas.device_references import (
    DeviceWorkflowReference,
    DeviceWorkflowReferences,
)


def _pins_device(definition: Mapping[str, JsonValue], device_id: str) -> bool:
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "agent":
            continue
        data = node.get("data")
        if (
            isinstance(data, dict)
            and data.get("expected_device_id") == device_id
        ):
            return True
    return False


async def list_device_workflow_references(
    db: AsyncSession,
    project_id: str,
    device_id: UUID,
    *,
    limit: int = 5,
    offset: int = 0,
) -> DeviceWorkflowReferences:
    """
    A live reference uses the fixed release, never an unpublished editor graph.
    """
    selected_device = str(device_id)
    # The text predicate narrows database-to-service transfer; the structured
    # node check below remains the authority and rejects unrelated matches.
    drafts = (
        await db.execute(
            select(Workflow.id, Workflow.name, Workflow.definition).where(
                Workflow.project_id == project_id,
                Workflow.status != "archived",
                cast(Workflow.definition, String).contains(selected_device),
            )
        )
    ).all()
    releases = (
        await db.execute(
            select(Workflow.id, Workflow.name, WorkflowRelease.definition)
            .join(
                WorkflowRelease,
                (WorkflowRelease.workflow_id == Workflow.id)
                & (WorkflowRelease.version == Workflow.published_version),
            )
            .where(
                Workflow.project_id == project_id,
                WorkflowRelease.project_id == project_id,
                Workflow.status == "active",
                cast(WorkflowRelease.definition, String).contains(
                    selected_device
                ),
            )
        )
    ).all()
    matches: dict[str, tuple[str, set[str]]] = {}
    for workflow_id, name, definition in drafts:
        if _pins_device(definition, selected_device):
            matches[workflow_id] = (name, {"draft"})
    for workflow_id, name, definition in releases:
        if _pins_device(definition, selected_device):
            if workflow_id in matches:
                matches[workflow_id][1].add("published")
            else:
                matches[workflow_id] = (name, {"published"})
    references = [
        DeviceWorkflowReference(
            workflow_id=workflow_id,
            name=name,
            source=(
                "both"
                if len(sources) == 2
                else "published" if "published" in sources else "draft"
            ),
        )
        for workflow_id, (name, sources) in sorted(
            matches.items(),
            key=lambda item: (item[1][0], item[0]),
        )
    ]
    return DeviceWorkflowReferences(
        references=references[offset:offset + limit],
        total=len(references),
    )

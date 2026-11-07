"Parse a parent workflow hop only after the internal channel is \
authenticated."

import json
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request

from app.schemas.knowledge import KnowledgeChannel

MAX_WORKFLOW_DEPTH = 4


@dataclass(frozen=True)
class WorkflowParentContext:
    parent_execution_id: UUID
    workflow_lineage: tuple[str, ...]


def trusted_workflow_parent(
    request: Request,
    channel: KnowledgeChannel | None,
) -> WorkflowParentContext | None:
    raw_lineage = request.headers.get("X-Workflow-Lineage")
    raw_parent = request.headers.get("X-Workflow-Parent-Execution-ID")
    if raw_lineage is None and raw_parent is None:
        return None
    if (
        channel != KnowledgeChannel.INTERNAL
        or not raw_lineage
        or not raw_parent
    ):
        raise HTTPException(403, "父工作流来源无法确认")
    try:
        lineage = json.loads(raw_lineage)
        parent_id = UUID(raw_parent)
        if (
            not isinstance(lineage, list)
            or not 1 <= len(lineage) <= MAX_WORKFLOW_DEPTH
            or any(
                not isinstance(item, str) or not 1 <= len(item) <= 128
                for item in lineage
            )
            or len(set(lineage)) != len(lineage)
        ):
            raise ValueError("Invalid workflow lineage")
    except (ValueError, TypeError):
        raise HTTPException(403, "父工作流调用链无效") from None
    return WorkflowParentContext(parent_id, tuple(lineage))

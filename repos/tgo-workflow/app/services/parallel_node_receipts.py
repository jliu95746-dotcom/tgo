"""Keep only cancellation timing after the run has already been stopped."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import NodeExecution, WorkflowExecution
from app.services.parallel_topology import parallel_plan_for


async def preserve_cancelled_parallel_node(
    db: AsyncSession,
    *,
    project_id: str,
    execution_id: str,
    node_id: str,
    node_type: str,
    started_at: datetime | None,
    completed_at: datetime | None,
    duration: int,
) -> bool:
    if (
        node_type not in {"api", "llm"}
        or started_at is None
        or completed_at is None
        or started_at.tzinfo is None
        or completed_at.tzinfo is None
        or completed_at < started_at
        or type(duration) is not int
        or duration < 0
    ):
        return False
    run = await db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.id == execution_id,
            WorkflowExecution.project_id == project_id,
            WorkflowExecution.status == "cancelled",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        await db.rollback()
        return False
    definition = run.definition_snapshot
    nodes = definition.get("nodes") if isinstance(definition, dict) else None
    edges = definition.get("edges") if isinstance(definition, dict) else None
    branch_ids: set[str] = set()
    if isinstance(nodes, list) and isinstance(edges, list):
        try:
            for node in nodes:
                if isinstance(node, dict) and node.get("type") == "parallel":
                    branch_ids.update(parallel_plan_for(str(node["id"]), nodes, edges).branch_ids)
        except (ValueError, KeyError, TypeError):
            branch_ids.clear()
    if (
        not isinstance(nodes, list)
        or node_id not in branch_ids
        or not any(
            isinstance(node, dict) and node.get("id") == node_id and node.get("type") == node_type
            for node in nodes
        )
    ):
        await db.rollback()
        return False
    existing = await db.scalar(
        select(NodeExecution.id).where(
            NodeExecution.execution_id == execution_id,
            NodeExecution.project_id == project_id,
            NodeExecution.node_id == node_id,
        )
    )
    if existing is None:
        db.add(
            NodeExecution(
                project_id=project_id,
                execution_id=execution_id,
                node_id=node_id,
                node_type=node_type,
                status="cancelled",
                input={"capture_status": "redacted"},
                output=None,
                error="并行分支已停止。",
                duration=duration,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
    await db.commit()
    return True

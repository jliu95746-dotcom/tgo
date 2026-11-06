"""Release continuation payloads only after the owning run has stopped."""

from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import WorkflowApprovalRequest
from app.models.execution import WorkflowExecution
from app.services.approval_preview import approval_preview_digest

TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "rejected",
        "expired",
    }
)


def terminal_checkpoint_receipt(
    request: WorkflowApprovalRequest,
) -> dict[str, JsonValue]:
    return {
        "capture_status": "redacted",
        "approval_preview_digest": approval_preview_digest(
            request.id,
            request.execution_id,
            request.summary,
        ),
    }


async def scrub_terminal_approval_checkpoints(
    db: AsyncSession,
    project_id: str,
    execution_id: str,
) -> None:
    """Scrub a run's approvals in the transaction that ends it."""
    await db.flush()
    state = (
        await db.execute(
            select(WorkflowExecution.status, WorkflowExecution.completed_at)
            .where(
                WorkflowExecution.id == execution_id,
                WorkflowExecution.project_id == project_id,
            )
            .with_for_update()
        )
    ).one_or_none()
    if state is None or state[0] not in TERMINAL_EXECUTION_STATUSES:
        return
    requests = await db.scalars(
        select(WorkflowApprovalRequest)
        .where(
            WorkflowApprovalRequest.project_id == project_id,
            WorkflowApprovalRequest.execution_id == execution_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for request in requests:
        if request.status == "pending":
            request.status = "cancelled"
            request.decided_at = state[1] or datetime.now(UTC)
        request.checkpoint = terminal_checkpoint_receipt(request)
        request.resume_checkpoint = None

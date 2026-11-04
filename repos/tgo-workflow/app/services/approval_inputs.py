"""Compare a checkpoint against the real immutable invocation input."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution import WorkflowExecution
from app.services.execution_snapshot import definition_digest
from app.services.event_inputs import frozen_event_inputs
from app.services.webhook_crypto import WebhookCryptoError
from app.services.webhook_runtime import frozen_webhook_inputs
from app.services.workflow_inputs import validate_inputs


async def approval_input_digest(
    db: AsyncSession,
    run: WorkflowExecution,
) -> str:
    if run.definition_snapshot is None:
        raise ValueError("审批冻结输入缺少流程定义。")
    try:
        if run.source_channel == "external_webhook":
            inputs = await frozen_webhook_inputs(db, run)
        elif run.source_channel == "internal_event":
            inputs = frozen_event_inputs(run)
        else:
            inputs = run.input or {}
        return definition_digest(
            validate_inputs(run.definition_snapshot, inputs)
        )
    except (ValueError, WebhookCryptoError):
        raise ValueError("审批冻结输入无法核验。") from None

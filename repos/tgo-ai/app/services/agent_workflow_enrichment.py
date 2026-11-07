"Add remote workflow details without losing employee-owned binding \
records."

import logging
from uuid import UUID

from app.models.agent import Agent
from app.models.workflow import AgentWorkflow
from app.services.workflow_service import WorkflowData, workflow_service_client

logger = logging.getLogger(__name__)


def _local_placeholder(binding: AgentWorkflow) -> WorkflowData:
    (
        'Represent a retained binding when its workflow details are '
        'unavailable.'
    )
    return WorkflowData(
        id=binding.workflow_id,
        name=binding.workflow_id,
        status="unavailable",
        version=0,
        updated_at=None,
        enabled=binding.enabled,
        bound_release_version=binding.release_version,
        bound_release_id=binding.release_id,
        bound_release_digest=binding.release_digest,
        details_available=False,
    )


async def enrich_agent_workflow_bindings(
    agents: list[Agent],
    project_id: UUID,
) -> list[Agent]:
    (
        'Use local associations as the source of truth for editable workflow '
        'IDs.'
    )
    workflow_ids = sorted(
        {
            binding.workflow_id
            for agent in agents
            for binding in agent.workflows
            if binding.deleted_at is None
        }
    )
    remote_by_id: dict[str, WorkflowData] = {}
    if workflow_ids:
        try:
            remote_workflows = (
                await workflow_service_client.get_workflows_batch(
                    workflow_ids,
                    str(project_id),
                )
            )
            remote_by_id = {item.id: item for item in remote_workflows}
        except Exception:  # noqa: BLE001 - local associations remain readable
            logger.warning(
                (
                    'Workflow details unavailable; retaining local employee '
                    'bindings'
                ),
                extra={
                    "project_id": str(project_id),
                    "binding_count": len(workflow_ids),
                },
            )

    for agent in agents:
        details: list[WorkflowData] = []
        for binding in agent.workflows:
            if binding.deleted_at is not None:
                continue
            remote_details = remote_by_id.get(binding.workflow_id)
            if remote_details is None:
                details.append(_local_placeholder(binding))
            else:
                details.append(
                    remote_details.model_copy(
                        update={
                            "enabled": binding.enabled,
                            "bound_release_version": binding.release_version,
                            "bound_release_id": binding.release_id,
                            "bound_release_digest": binding.release_digest,
                            "details_available": True,
                        }
                    )
                )
        setattr(agent, "_workflow_data", details)
    return agents

"""Only the current owned run may observe a capacity-exhausted approval."""

from datetime import UTC, datetime

from app.models.agent_activation_policy import AgentActivationPolicy
from app.schemas.agent_activation_readiness import AgentActivationReadiness
from app.schemas.agent_execution import AgentExecutionReference
from app.schemas.knowledge import KnowledgeChannel
from app.services.agent_execution_scope import current_execution_scope


def allows_capacity_observation(
    proof: AgentActivationReadiness,
    expected: AgentExecutionReference | None,
    policies: list[AgentActivationPolicy],
) -> bool:
    # This is never an enablement result or permission for a new dispatch.
    from app.services.agent_execution_reference import policy_revision

    scope = current_execution_scope()
    if (
        expected is None or scope is None or scope.closed or scope.revoked
        or scope.reference != expected
        or expected.channel != KnowledgeChannel.INTERNAL
        or proof.project_id != expected.project_id
        or proof.agent_id != expected.agent_id
        or proof.configuration_fingerprint
        != expected.configuration_fingerprint
        or not proof.business_evidence_revision
        or proof.business_evidence_revision
        != expected.business_evidence_revision
        or policy_revision(policies, expected.channel)
        != expected.policy_revision
        or not proof.blockers or any(
            item.code != "script_capacity_exhausted"
            or item.channel != KnowledgeChannel.INTERNAL
            or item.resource != "device" for item in proof.blockers
        )
        or len(proof.channels) != 1
    ):
        return False
    channel = proof.channels[0]
    return (
        channel.channel == KnowledgeChannel.INTERNAL
        and channel.template_id == "assistant"
        and channel.status == "passed"
        and channel.valid_until is not None
        and channel.valid_until.tzinfo is not None
        and channel.valid_until > datetime.now(UTC)
    )

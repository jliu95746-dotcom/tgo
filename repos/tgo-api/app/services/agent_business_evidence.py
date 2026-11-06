"""Read historical signed binding evidence without issuing any customer session."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.business_customer_identity import BusinessCustomerBinding
from app.schemas.agent_business_reviews import (
    BusinessTestEvidence,
    BusinessTestEvidenceQuery,
)
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
)
from app.services.business_customer_identity_crypto import (
    CustomerBindingVault,
    CustomerIssuerVault,
    utc,
)


def current_evidence(
    db: Session,
    query: BusinessTestEvidenceQuery,
    *,
    identity: BusinessCustomerIdentityService | None = None,
) -> BusinessTestEvidence:
    service = identity or BusinessCustomerIdentityService(db)
    service._require_admin(query.project_id, query.requested_by)
    issue = None
    try:
        connection = service._connection(query.project_id, query.connection_id)
        policy = service._policy(query.project_id, query.connection_id)
        original = query.customer_evidence
        binding = db.scalar(
            select(BusinessCustomerBinding)
            .where(
                BusinessCustomerBinding.id == original.binding_id,
                BusinessCustomerBinding.project_id == query.project_id,
                BusinessCustomerBinding.connection_id == query.connection_id,
            )
            .execution_options(populate_existing=True)
        )
        if (
            binding is None
            or policy is None
            or binding.revoked_at is not None
            or not binding.proof_digest
            or binding.revision != original.binding_revision
            or binding.policy_revision != original.policy_revision
            or binding.policy_id != policy.id
            or binding.connection_configuration_revision
            != query.configuration_revision
            or connection.configuration_revision
            != query.configuration_revision
            or utc(binding.expires_at) != original.expires_at
            or not utc(binding.issued_at)
            <= query.observed_at
            < utc(binding.expires_at)
            or query.observed_at > datetime.now(UTC)
        ):
            issue = "identity_changed"
        else:
            service._valid_scope(
                connection,
                policy,
                query.project_id,
                binding.visitor_id,
                binding.platform_id,
                binding.policy_revision,
            )
            # Validate private storage availability locally. No credential or
            # customer value is returned, and expired sessions are not renewed.
            if (
                CustomerBindingVault()
                .decrypt(
                    binding.customer_ciphertext, query.project_id, binding.id
                )
                .token
                is None
                or CustomerIssuerVault()
                .decrypt(
                    policy.signing_ciphertext, query.project_id, policy.id
                )
                .token
                is None
            ):
                issue = "identity_unavailable"
    except TGOAPIException as error:
        issue = (
            "identity_changed"
            if error.status_code in {403, 404, 409}
            else "identity_unavailable"
        )
    return BusinessTestEvidence.model_validate(
        {
            **query.model_dump(),
            "status": "changed" if issue else "current",
            "issues": [issue] if issue else [],
            "checked_at": datetime.now(UTC),
        }
    )

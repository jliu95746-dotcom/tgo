"Private test purpose, separate service identities, current merchant \
customer proof."

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.business_trial_route import BusinessTrialRoute
from app.api.internal.endpoints.agent_activation import (
    require_activation_service,
)
from app.api.internal.endpoints.business_connections import (
    require_business_adapter,
)
from app.core.database import get_db
from app.schemas.agent_configuration_actor import AgentConfigurationActorQuery
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialReceipt,
    BusinessTrialCustomerEvidence,
)
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot
from app.schemas.business_trial_resolution import BusinessTrialResolution
from app.services.agent_configuration_actor import current_configuration_actor
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
)
from app.services import (
    business_connections,
    agent_business_trial_client,
    business_trial_query_client,
)
from app.services.ai_client import ai_client

router = APIRouter(route_class=BusinessTrialRoute)


def actor_check(db: Session, command: BusinessTrialCommand) -> None:
    current_configuration_actor(
        db,
        AgentConfigurationActorQuery(
            project_id=command.project_id, requested_by=command.actor_id
        ),
    )


@router.post(
    "/ai/business-trials/query",
    response_model=BusinessTrialReceipt,
    dependencies=[Depends(require_activation_service)],
)
async def query(
    command: BusinessTrialCommand, db: Session = Depends(get_db)
) -> BusinessTrialReceipt:
    actor_check(db, command)
    BusinessCustomerIdentityService(db).resolve(
        command.project_id,
        command.connection_id,
        command.visitor_id,
        command.customer_session,
    )
    return await business_trial_query_client.query(command)


@router.post(
    "/business-connections/{connection_id}/business-trial",
    response_model=BusinessTrialResolution,
    dependencies=[Depends(require_business_adapter)],
)
async def resolve(
    connection_id: UUID,
    check: BusinessTrialCheck,
    db: Session = Depends(get_db),
) -> BusinessTrialResolution:
    command = check.command
    if connection_id != command.connection_id:
        raise HTTPException(409, "原业务测试连接不一致")
    actor_check(db, command)
    connection, credentials = business_connections.resolve(
        db,
        command.project_id,
        connection_id,
        expected_revision=command.expected_revision,
    )
    if (
        not connection.enabled
        or connection.mode != "http"
        or command.operation not in connection.capabilities
        or connection.configuration_revision != command.configuration_revision
        or connection.authorization_revision != command.authorization_revision
        or connection.resource_revision != command.resource_revision
    ):
        raise HTTPException(409, "原业务测试连接或授权已改变")
    identity = BusinessCustomerIdentityService(db).resolve(
        command.project_id,
        connection_id,
        command.visitor_id,
        command.customer_session,
    )
    authority = await agent_business_trial_client.authorize(ai_client, check)
    # Re-read after the awaited employee check. No private credential is
    # released
    # for a customer/session revoked while that RPC was running.
    actor_check(db, command)
    connection, credentials = business_connections.resolve(
        db,
        command.project_id,
        connection_id,
        expected_revision=command.expected_revision,
    )
    current = BusinessCustomerIdentityService(db).resolve(
        command.project_id,
        connection_id,
        command.visitor_id,
        command.customer_session,
    )
    if current != identity:
        raise HTTPException(409, "原业务测试客户身份已改变")
    return BusinessTrialResolution(
        authority=authority,
        snapshot=BusinessConnectionPrivateSnapshot(
            connection=connection, credentials=credentials
        ),
        customer_id=current.customer_id,
        customer_evidence=BusinessTrialCustomerEvidence(
            binding_id=current.binding_id,
            binding_revision=current.revision,
            policy_revision=current.policy_revision,
            expires_at=current.expires_at,
        ),
    )

"""Resolve private credentials only for the original serving employee and customer."""

from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.business_customer_identity import BusinessCustomerBinding
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionCheck,
    BusinessExecutionReceipt,
)
from app.schemas.agent_configuration_actor import AgentConfigurationActorQuery
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot
from app.schemas.business_execution_resolution import (
    BusinessExecutionResolution,
)
from app.services import (
    business_connections,
    agent_business_execution_client,
    business_execution_query_client,
)
from app.services.agent_configuration_actor import current_configuration_actor
from app.services.business_execution_context import (
    BusinessExecutionContextService,
)
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
)
from app.services.business_customer_identity_crypto import issue_session
from app.services.ai_client import ai_client


async def query_execution(
    db: Session, command: BusinessExecutionCommand
) -> BusinessExecutionReceipt:
    context = BusinessExecutionContextService(db)
    original = context.verify(command.context)
    if original.context_digest != command.context_digest:
        raise HTTPException(403, "原客户会话依据不一致")
    result = await business_execution_query_client.query(command)
    current = context.verify(command.context)
    if current != original.model_copy(
        update={"checked_at": current.checked_at}
    ):
        raise HTTPException(409, "查询期间原客户会话已改变")
    return result


async def resolve_execution(
    db: Session, connection_id: UUID, check: BusinessExecutionCheck
) -> BusinessExecutionResolution:
    command = check.command
    if command.connection_id != connection_id:
        raise HTTPException(409, "原业务连接范围不一致")
    original = BusinessExecutionContextService(db).verify(command.context)
    if original.context_digest != command.context_digest:
        raise HTTPException(403, "原客户会话依据不一致")
    authority = await agent_business_execution_client.authorize(
        ai_client, check
    )
    current_configuration_actor(
        db,
        AgentConfigurationActorQuery(
            project_id=command.reference.project_id,
            requested_by=authority.actor_id,
        ),
    )
    current = BusinessExecutionContextService(db).verify(command.context)
    if current != original.model_copy(
        update={"checked_at": current.checked_at}
    ):
        raise HTTPException(409, "核验期间客户会话已改变")
    connection, credentials = business_connections.resolve(
        db,
        command.reference.project_id,
        connection_id,
        expected_revision=authority.connection_revision,
    )
    if (
        not connection.enabled
        or connection.mode != "http"
        or command.operation not in connection.capabilities
        or connection.configuration_revision
        != authority.configuration_revision
        or connection.authorization_revision
        != authority.authorization_revision
        or connection.resource_revision != authority.resource_revision
    ):
        raise HTTPException(409, "业务连接或允许操作已变化")
    evidence = next(
        (
            row
            for row in current.bindings
            if row.connection_id == connection_id
        ),
        None,
    )
    row = (
        db.get(
            BusinessCustomerBinding,
            evidence.binding_id,
            populate_existing=True,
        )
        if evidence
        else None
    )
    if row is None:
        raise HTTPException(403, "客户尚未验证该业务连接")
    customer = BusinessCustomerIdentityService(db).resolve(
        command.reference.project_id,
        connection_id,
        command.context.visitor_id,
        issue_session(row).session_token,
    )
    if (
        evidence is None
        or customer.revision != evidence.binding_revision
        or customer.platform_id != current.platform_id
    ):
        raise HTTPException(403, "当前商户客户身份无法确认")
    return BusinessExecutionResolution(
        authority=authority,
        principal=current,
        customer_id=customer.customer_id,
        snapshot=BusinessConnectionPrivateSnapshot(
            connection=connection, credentials=credentials
        ),
    )

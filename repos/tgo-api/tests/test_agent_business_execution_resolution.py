"""A real signed request cannot expose credentials after session/role changes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.core.exceptions import TGOAPIException
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionCheck,
    BusinessExecutionAuthority,
)
from app.schemas.business_connections import (
    BusinessConnectionResponse,
    BusinessHTTPConfiguration,
    BusinessConnectionCredentials,
)
from app.services import agent_business_execution_resolution as service

pytest_plugins = ("tests.test_business_execution_context",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["order_query", "customer_profile_query", "membership_query"]
)
@pytest.mark.parametrize(
    "fault",
    [
        None,
        "closed_session",
        "different_attempt",
        "actor_revoked",
        "connection_changed",
    ],
)
async def test_private_resolution_rechecks_original_database_state_after_ai(
    context_fixture, monkeypatch, fault, operation
):
    context, db, run, conversation, session, auth, check, _ = context_fixture
    command = BusinessExecutionCommand(
        query_id=uuid4(),
        reference={
            "project_id": run.project_id,
            "agent_id": uuid4(),
            "channel": "web",
            "configuration_fingerprint": "a" * 64,
            "policy_revision": "b" * 64,
            "business_evidence_revision": "c" * 64,
        },
        connection_id=session.connection_id,
        binding_id=uuid4(),
        binding_revision=1,
        operation=operation,
        context_digest=auth.context_digest,
        order_no=SecretStr("ORDER-001") if operation == "order_query" else None,
        context=check,
    )
    authority = BusinessExecutionAuthority(
        **command.identity(),
        actor_id=uuid4(),
        phase="before_query",
        connection_revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        report_id=uuid4(),
        review_id=uuid4(),
        configuration_test_id=uuid4(),
        checked_at=datetime.now(UTC),
        expires_at=auth.expires_at,
    )
    monkeypatch.setattr(service, "BusinessExecutionContextService", lambda _: context)
    monkeypatch.setattr(
        service, "BusinessCustomerIdentityService", lambda _: context.identity
    )
    actor = Mock(side_effect=HTTPException(403) if fault == "actor_revoked" else None)
    monkeypatch.setattr(service, "current_configuration_actor", actor)

    async def authorize(*args):
        if fault == "closed_session":
            conversation.status = "closed"
        elif fault == "different_attempt":
            run.response_client_msg_no = "replacement-attempt"
        db.commit()
        return authority

    monkeypatch.setattr(
        service.agent_business_execution_client,
        "authorize",
        AsyncMock(side_effect=authorize),
    )
    connection = BusinessConnectionResponse(
        id=session.connection_id,
        project_id=run.project_id,
        name="测试商户连接",
        mode="http",
        capabilities=["order_query", "customer_profile_query", "membership_query"],
        configuration=BusinessHTTPConfiguration(
            base_url="https://fixture.test", auth_mode="bearer"
        ),
        enabled=True,
        credentials_configured=True,
        revision=1,
        configuration_revision=1,
        authorization_revision=2 if fault == "connection_changed" else 1,
        resource_revision="a" * 64,
        test_status="passed",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    credentials = BusinessConnectionCredentials(
        token=SecretStr("private-server-credential")
    )
    read = Mock(return_value=(connection, credentials))
    monkeypatch.setattr(service.business_connections, "resolve", read)
    if fault:
        with pytest.raises((HTTPException, TGOAPIException)):
            await service.resolve_execution(
                db,
                command.connection_id,
                BusinessExecutionCheck(command=command, phase="before_query"),
            )
        if fault != "connection_changed":
            read.assert_not_called()
    else:
        result = await service.resolve_execution(
            db,
            command.connection_id,
            BusinessExecutionCheck(command=command, phase="before_query"),
        )
        assert result.customer_id.get_secret_value() == "merchant-member-private-001"
        assert result.principal.visitor_session_id == conversation.id
        assert actor.call_args.args[1].requested_by == authority.actor_id
        assert read.call_args.kwargs == {"expected_revision": 1}

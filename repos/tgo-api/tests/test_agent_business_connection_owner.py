"""No credential-bearing configuration is returned to the AI owner caller."""
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.schemas.agent_business_connections import (
    BusinessConnectionOwner,
    ConnectionOwnerQuery,
    AgentBusinessConnectionRecord,
    AgentBusinessConnectionWrite,
)
from app.services import agent_business_connection_owner as owner
from app.services import agent_business_connection_client as client


def test_owner_uses_current_permission_and_only_explicit_metadata(monkeypatch):
    query = ConnectionOwnerQuery(
        project_id=uuid4(), requested_by=uuid4(), connection_id=uuid4()
    )
    permission = Mock()
    monkeypatch.setattr(owner, "require_activation_actor", permission)
    connection = Mock(
        name="fixture",
        mode="http",
        enabled=False,
        capabilities=["order_query"],
        revision=4,
        configuration_revision=2,
        authorization_revision=3,
        resource_revision="a" * 64,
        test_status="not_tested",
    )
    connection.name = "订单连接"
    monkeypatch.setattr(owner, "get", Mock(return_value=connection))
    monkeypatch.setattr(
        owner,
        "operation_configuration",
        Mock(
            return_value=[
                {
                    "operation": "order_query",
                    "report_id": None,
                    "status": "not_tested",
                    "valid_until": None,
                    "issues": [],
                }
            ]
        ),
    )
    db = Mock()
    result = owner.current_business_connection(db, query)
    permission.assert_called_once_with(db, query.project_id, query.requested_by)
    owner.get.assert_called_once_with(db, query.project_id, query.connection_id)
    assert not result.can_enable and result.scope == "business_connection_configuration"
    assert result.operations == ["order_query"] and result.revision == 4
    assert (
        not {"configuration", "credentials", "token", "password"}
        & result.model_dump().keys()
    )


def test_revoked_actor_never_reads_connection(monkeypatch):
    monkeypatch.setattr(
        owner, "require_activation_actor", Mock(side_effect=HTTPException(403))
    )
    read = Mock()
    monkeypatch.setattr(owner, "get", read)
    with pytest.raises(HTTPException):
        owner.current_business_connection(
            Mock(),
            ConnectionOwnerQuery(
                project_id=uuid4(), requested_by=uuid4(), connection_id=uuid4()
            ),
        )
    read.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", ["project", "actor", "operation", "revision", "enable", "missing"]
)
async def test_proxy_rejects_foreign_or_different_save_receipt(bad):
    project, actor, agent, connection = [uuid4() for _ in range(4)]
    command = AgentBusinessConnectionWrite(
        request_id=uuid4(), expected_revision=0, operations=["order_query"]
    )
    record = AgentBusinessConnectionRecord(
        id=uuid4(),
        project_id=project,
        agent_id=agent,
        connection_id=connection,
        operations=["order_query"],
        enabled=True,
        revision=1,
        configuration_revision=1,
        authorization_revision=1,
        resource_revision="a" * 64,
        configured_by=actor,
    ).model_dump(mode="json")
    if bad == "project":
        record["project_id"] = str(uuid4())
    if bad == "actor":
        record["configured_by"] = str(uuid4())
    if bad == "operation":
        record["operations"] = ["logistics_query"]
    if bad == "revision":
        record["revision"] = 2
    if bad == "enable":
        record["can_enable"] = True
    if bad == "missing":
        del record["can_enable"]

    class Transport:
        def _trial_headers(self, p, a):
            return {"project": str(p), "actor": str(a)}

        async def _make_request(self, method, path, **kwargs):
            assert method == "PUT" and str(agent) in path and str(connection) in path
            assert kwargs["extra_headers"]["actor"] == str(actor)
            return httpx.Response(200, json=record)

    with pytest.raises(HTTPException):
        await client.save(Transport(), project, actor, agent, connection, command)


def test_api_and_ai_business_contracts_are_identical():
    from pathlib import Path

    api = Path(__file__).parents[1] / "app/schemas/agent_business_connections.py"
    ai = api.parents[3] / "tgo-ai/app/schemas/agent_business_connections.py"
    assert api.read_bytes() == ai.read_bytes()


def test_customer_operations_can_be_bound_only_as_explicit_read_capabilities():
    capabilities = [
        "order_query",
        "customer_profile_query",
        "membership_query",
        "unexpected_write",
    ]
    assert owner.supported_operations(capabilities) == [
        "order_query",
        "customer_profile_query",
        "membership_query",
    ]
    command = AgentBusinessConnectionWrite(
        request_id=uuid4(),
        expected_revision=0,
        operations=["customer_profile_query", "membership_query"],
    )
    assert command.operations == ["customer_profile_query", "membership_query"]
    with pytest.raises(ValueError):
        AgentBusinessConnectionWrite(
            request_id=uuid4(),
            expected_revision=0,
            operations=["customer_profile_query", "customer_profile_query"],
        )

"""Audit response construction preserves validation and tenant filtering."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.domain.business_tools.models import message_lookup_digest
from app.schemas.business_query_audit import BusinessMessageAuditQuery
from app.services.business_query_audit_read import (
    list_message_query_audits,
    list_query_audits,
)


def audit_fixture(**updates):
    project, request_id, actor_id = uuid4(), uuid4(), uuid4()
    query = BusinessMessageAuditQuery(
        project_id=project,
        source_message_id="private-source",
        response_client_msg_no="private-response",
    )
    row = SimpleNamespace(
        id=uuid4(),
        tenant_id=project,
        request_id=str(request_id),
        actor_id=str(actor_id),
        operation="order_query",
        outcome="success",
        duration_ms=12.5,
        created_at=datetime.now(UTC),
        source_message_digest=message_lookup_digest(
            project, query.source_message_id
        ),
        response_message_digest=message_lookup_digest(
            project, query.response_client_msg_no
        ),
        parameter_fingerprint="private-fingerprint",
    )
    for name, value in updates.items():
        setattr(row, name, value)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=1),
        execute=AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [row]),
            )
        ),
    )
    return row, query, db, request_id, actor_id


async def read_audit(message, db, query, request_id):
    if message:
        return await list_message_query_audits(db, query)
    return await list_query_audits(db, query.project_id, request_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [False, True])
@pytest.mark.parametrize(
    "operation,outcome",
    [
        ("order_query", "success"),
        ("logistics_query", "denied"),
        ("customer_profile_query", "timeout"),
        ("membership_query", "failed"),
    ],
)
async def test_audit_response_preserves_typed_identity_and_safe_metadata(
    message,
    operation,
    outcome,
):
    row, query, db, request_id, actor_id = audit_fixture(
        operation=operation,
        outcome=outcome,
    )
    result = await read_audit(message, db, query, request_id)
    assert result.total == 1 and len(result.events) == 1
    event = result.events[0]
    assert event.id == row.id and event.project_id == query.project_id
    assert event.query_id == request_id and event.agent_id == actor_id
    assert event.operation == operation and event.outcome == outcome
    assert event.duration_ms == 12.5 and event.created_at == row.created_at
    assert "private-" not in result.model_dump_json()
    sql = str(db.execute.await_args.args[0])
    assert "pg_business_query_audit.tenant_id" in sql
    assert "pg_business_query_audit.id" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [False, True])
@pytest.mark.parametrize(
    "invalid",
    [
        {"operation": "refund"},
        {"outcome": "unknown"},
        {"actor_id": "invalid-id"},
        {"duration_ms": -1},
    ],
    ids=[
        "write-operation",
        "unknown-outcome",
        "invalid-actor",
        "negative-time",
    ],
)
async def test_audit_response_rejects_untrusted_persisted_values(
    message,
    invalid,
):
    _, query, db, request_id, _ = audit_fixture(**invalid)
    with pytest.raises(ValueError):
        await read_audit(message, db, query, request_id)

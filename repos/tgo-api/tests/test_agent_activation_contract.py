"""Gateway must preserve activation in requests and documented responses."""
from uuid import uuid4
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from app.schemas.ai import AgentCreateRequest, AgentUpdateRequest, AgentResponse


def test_gateway_forwards_explicit_false_without_other_mutations() -> None:
    assert AgentUpdateRequest(is_active=False).model_dump(exclude_unset=True) == {"is_active": False}
    assert AgentUpdateRequest().model_dump(exclude_unset=True) == {}


def test_gateway_rejects_null_activation() -> None:
    with pytest.raises(ValidationError):
        AgentUpdateRequest(is_active=None)


def test_gateway_creation_and_response_preserve_activation() -> None:
    assert AgentCreateRequest(name="验收", model="fixture", is_active=False).is_active is False
    now = datetime.now(timezone.utc)
    response = AgentResponse(id=uuid4(), name="验收", model="fixture", is_active=False,
                             created_at=now, updated_at=now, deleted_at=None)
    assert response.is_active is False

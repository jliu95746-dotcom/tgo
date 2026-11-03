"""A setup draft cannot activate an employee or smuggle remote execution."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.agent_drafts import AgentDraftRequest


def payload(**changes):
    return {"revision": 1, "agent": {"name": "待测试客服", "model": "__system_default__",
            "is_active": False, "is_default": False, **changes}}


@pytest.mark.parametrize("changes", [
    {"is_active": True}, {"is_default": True}, {"is_remote_store_agent": True},
    {"remote_agent_url": "https://example.invalid"}, {"store_agent_id": "remote"},
    {"agent_category": "computer_use"}, {"name": "  "},
    {"tools": [{"tool_id": str(uuid4()), "config": {"api_key": "fixture-secret"}}]},
])
def test_draft_cannot_activate_or_supply_execution_credentials(changes):
    with pytest.raises(ValidationError):
        AgentDraftRequest.model_validate(payload(**changes))


def test_draft_requires_positive_revision_and_strict_identity():
    valid = AgentDraftRequest.model_validate(payload())
    assert valid.revision == 1 and valid.agent.is_active is False
    for changes in [{"revision": 0}, {"project_id": str(uuid4())}, {"created_by": str(uuid4())}]:
        with pytest.raises(ValidationError):
            AgentDraftRequest.model_validate({**payload(), **changes})


def test_draft_selection_is_explicit_strict_and_cannot_supply_approval():
    selection = {"template_id": "knowledge", "template_version": 1, "channel": "wecom_kf"}
    request = AgentDraftRequest.model_validate({**payload(), "activation_selection": selection})
    assert request.activation_selection.channel.value == "wecom_kf"
    for changed in [{"template_version": True}, {"can_enable": True}, {"channel": "unknown"}]:
        with pytest.raises(ValidationError):
            AgentDraftRequest.model_validate({**payload(), "activation_selection": {**selection, **changed}})

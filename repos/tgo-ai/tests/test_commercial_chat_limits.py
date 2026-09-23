"""Auxiliary intent/workflow chat calls share the platform model ceilings."""

from uuid import uuid4
import pytest

from app.config import settings
from app.schemas.chat import ChatCompletionRequest
from app.services.commercial_ai_limits import constrain_chat
from app.services.quota_authorization import metered_execution


@pytest.fixture
def metered(monkeypatch):
    token = metered_execution.set(True)
    monkeypatch.setattr(settings, "saas_approved_models", ["synthetic-model"])
    try:
        yield
    finally:
        metered_execution.reset(token)


def request(**overrides):
    values = {
        "provider_id": uuid4(),
        "model": "synthetic-model",
        "messages": [{"role": "user", "content": "hello"}],
    }
    return ChatCompletionRequest(**(values | overrides))


def test_chat_cannot_raise_output_tools_or_parallel_choice_cost(metered):
    source = request(max_tokens=100000, max_tool_rounds=20, n=10)
    bounded = constrain_chat(source)
    assert bounded.max_tokens == settings.saas_max_output_tokens
    assert bounded.max_tool_rounds <= settings.saas_max_tool_calls
    assert bounded.n == 1
    assert source.n == 10


def test_chat_rejects_unapproved_model_and_oversized_input(metered):
    with pytest.raises(ValueError):
        constrain_chat(request(model="unapproved"))
    with pytest.raises(ValueError):
        constrain_chat(
            request(
                messages=[
                    {
                        "role": "user",
                        "content": "x" * (settings.saas_max_input_characters + 1),
                    }
                ]
            )
        )


def test_legacy_chat_unchanged():
    token = metered_execution.set(False)
    try:
        original = request(model="legacy", n=2)
        assert constrain_chat(original) is original
    finally:
        metered_execution.reset(token)

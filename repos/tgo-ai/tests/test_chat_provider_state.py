"""Provider context round trips without changing plain messages."""

import json
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.schemas.chat import (
    ChatCompletionRequest,
    ChatMessage,
    ChoiceMessage,
    DeltaMessage,
)
from app.services.commercial_ai_limits import constrain_chat
from app.services.quota_authorization import metered_execution


def state() -> dict[str, object]:
    return {
        "provider": "google",
        "payload": '[{"role":"model","parts":[{"thought_signature":'
        '"fixture-hidden-signature","text":"hello"}]}]',
        "call_ids": [],
    }


@pytest.mark.parametrize("model", [ChatMessage, ChoiceMessage, DeltaMessage])
def test_provider_context_survives_message_round_trip(
    model: type[BaseModel],
) -> None:
    source = {
        "role": "assistant",
        "content": "hello",
        "provider_state": state(),
    }
    message = model.model_validate(source)
    encoded = json.loads(message.model_dump_json())
    assert encoded["provider_state"] == state()
    assert (
        json.loads(ChatMessage.model_validate(encoded).model_dump_json())[
            "provider_state"
        ]
        == state()
    )
    assert "fixture-hidden-signature" not in repr(message)
    for mode in ("validation", "serialization"):
        schema = model.model_json_schema(mode=mode)
        assert "role" in schema["properties"]
        assert "provider_state" in schema["properties"]


def test_plain_delta_wire_format_remains_unchanged() -> None:
    assert DeltaMessage().model_dump_json() == (
        '{"role":null,"content":null,"tool_calls":null}'
    )
    assert "provider_state" not in ChatMessage(role="user").model_dump()
    assert "provider_state" not in ChoiceMessage().model_dump()


@pytest.mark.parametrize(
    "change",
    [
        {"provider": "unknown"},
        {"payload": "x" * 524289},
        {"call_ids": ["duplicate", "duplicate"]},
        {"unexpected": "must-not-be-accepted"},
    ],
)
def test_invalid_context_is_rejected_at_request_boundary(
    change: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ChatMessage.model_validate(
            {
                "role": "assistant",
                "provider_state": state() | change,
            }
        )


def test_provider_context_is_included_in_existing_input_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = metered_execution.set(True)
    monkeypatch.setattr(settings, "saas_approved_models", ["fixture-model"])
    monkeypatch.setattr(settings, "saas_max_input_characters", 2000)
    try:
        request = ChatCompletionRequest.model_validate(
            {
                "provider_id": str(uuid4()),
                "model": "fixture-model",
                "messages": [
                    {"role": "user", "content": "hello"},
                    {
                        "role": "assistant",
                        "content": "hello",
                        "provider_state": state() | {"payload": "x" * 3000},
                    },
                ],
            }
        )
        with pytest.raises(ValueError, match="长度上限"):
            constrain_chat(request)
    finally:
        metered_execution.reset(token)

"""Public validation, defaults and serialized values stay compatible."""
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import ValidationError
import pytest

from app.schemas.ai_provider import AIProviderCreate, AIProviderResponse
from app.schemas.knowledge_versions import VersionChangeRequest
from app.schemas.message_analysis import AnalysisStageResult
from app.schemas.plugin import PluginInstallRequest
from app.schemas.search import SearchPagination
from app.schemas.visitor_activity import VisitorActivityCreateRequest
from app.schemas.wukongim import WuKongIMMessage


def test_provider_creation_keeps_mixed_model_inputs():
    request = AIProviderCreate.model_validate(
        {
            "provider": "openai",
            "name": "synthetic",
            "api_key": "synthetic-key",
            "available_models": [
                "chat-model",
                {"model_id": "embedding-model", "model_type": "embedding"},
            ],
        }
    )
    serialized = request.model_dump(mode="json")
    assert serialized["available_models"] == [
        "chat-model",
        {
            "model_id": "embedding-model",
            "model_type": "embedding",
            "capabilities": None,
        },
    ]


def test_provider_response_keeps_identifier_only_models_and_hides_secret():
    payload = {
        "id": uuid4(),
        "project_id": uuid4(),
        "provider": "openai",
        "name": "synthetic",
        "has_api_key": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "available_models": ["chat-model"],
    }
    response = AIProviderResponse.model_validate(payload)
    assert response.model_dump(mode="json")["available_models"] == [
        "chat-model"
    ]
    assert "api_key" not in response.model_dump()
    with pytest.raises(ValidationError):
        AIProviderResponse.model_validate(
            {**payload, "available_models": [{"model_id": "chat-model"}]}
        )


def test_install_and_knowledge_defaults_are_independent():
    payload = {
        "id": "synthetic",
        "name": "synthetic",
        "version": "1.0.0",
        "source": {"github": {"repo": "example/synthetic"}},
    }
    first = PluginInstallRequest.model_validate(payload)
    second = PluginInstallRequest.model_validate(payload)
    first.runtime.args.append("synthetic-argument")
    first.runtime.env["SYNTHETIC_FLAG"] = "true"
    assert second.runtime.args == [] and second.runtime.env == {}
    assert second.runtime.auto_restart is True
    assert second.runtime.restart_delay == 5
    left, right = VersionChangeRequest(), VersionChangeRequest()
    left.content.question = "synthetic question"
    assert right.content.question is None and right.action == "save"


@pytest.mark.parametrize(
    "model",
    [AIProviderCreate, PluginInstallRequest, VisitorActivityCreateRequest],
)
def test_required_request_fields_are_still_required(model):
    with pytest.raises(ValidationError):
        model.model_validate({})


def test_activity_examples_are_valid_request_values():
    baseline = {
        "visitor_id": str(uuid4()),
        "activity_type": "page_view",
        "title": "synthetic",
    }
    schema = VisitorActivityCreateRequest.model_json_schema()
    for name, field in schema["properties"].items():
        examples = field.get(
            "examples", [field["example"]] if "example" in field else []
        )
        for example in examples:
            VisitorActivityCreateRequest.model_validate(
                {**baseline, name: example}
            )
    with pytest.raises(ValidationError):
        VisitorActivityCreateRequest.model_validate(
            {**baseline, "duration_seconds": -1}
        )


def test_message_and_search_computed_fields_keep_wire_values():
    identifier = 9_223_372_036_854_775_000
    message = WuKongIMMessage.model_validate(
        {
            "header": {"no_persist": 0, "red_dot": 1, "sync_once": 0},
            "setting": (1 << 7) | (1 << 1),
            "message_id": identifier,
            "client_msg_no": "synthetic",
            "message_seq": 1,
            "from_uid": "synthetic",
            "channel_id": "synthetic",
            "channel_type": 251,
            "timestamp": 1_700_000_000,
            "payload": {"type": 1, "content": "synthetic"},
        }
    )
    output = message.model_dump(mode="json")
    assert output["message_id_str"] == str(identifier)
    assert output["setting_flags"]["receipt"] is True
    assert output["setting_flags"]["stream"] is True
    pages = SearchPagination(page=1, page_size=10, total=21).model_dump()
    assert pages["has_next"] is True and pages["has_previous"] is False


def test_analysis_success_cannot_omit_provider_evidence():
    with pytest.raises(ValidationError):
        AnalysisStageResult.model_validate(
            {"capability": "ocr", "status": "completed", "text": "synthetic"}
        )

"""Shared provider policy and tenant access boundaries."""

from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.api.company_configuration_access import (
    require_operator_model_management,
)
from app.core.exceptions import TGOAPIException
from app.models import AIProvider, ProjectAIConfig, SystemSetup
from app.schemas.platform_models import (
    PlatformModelDefinition,
    StoredPlatformModel,
)
from app.schemas.shared_models import (
    SharedModelsChange,
    SharedProviderChange,
    SharedModel,
    SharedSelection,
    StoredSharedModels,
    StoredSharedProvider,
)
from app.services.platform_models import runtime_model
from app.services.shared_models import (
    SHARED_KEY, _validate, apply_shared_models_to_project,
)
from app.utils.crypto import encrypt_str


def _catalogue() -> StoredSharedModels:
    chat_id, media_id = uuid4(), uuid4()
    models = {
        "chat": SharedSelection(provider_id=chat_id, model_id="chat-model"),
        **{
            purpose: SharedSelection(
                provider_id=media_id, model_id=f"{purpose}-model"
            )
            for purpose in ("embedding", "asr", "ocr", "vlm")
        },
    }
    return StoredSharedModels(
        version=1,
        defaults=models,
        providers=[
            StoredSharedProvider(
                id=chat_id,
                provider="deepseek",
                name="chat",
                api_base_url="https://api.example.com/v1",
                encrypted_api_key=encrypt_str("synthetic-chat-key"),
                models=[SharedModel(model_id="chat-model", model_type="chat")],
            ),
            StoredSharedProvider(
                id=media_id,
                provider="dashscope",
                name="media",
                api_base_url="https://media.example.com/v1",
                encrypted_api_key=encrypt_str("synthetic-media-key"),
                models=[
                    SharedModel(
                        model_id=f"{purpose}-model", model_type=purpose
                    )
                    for purpose in ("embedding", "asr", "ocr", "vlm")
                ],
            ),
        ],
    )


def test_shared_catalogue_requires_matching_defaults() -> None:
    catalogue = _catalogue()
    _validate(catalogue)
    catalogue.defaults["ocr"] = SharedSelection(
        provider_id=catalogue.providers[0].id, model_id="chat-model"
    )
    with pytest.raises(TGOAPIException) as error:
        _validate(catalogue)
    assert error.value.code == "SHARED_MODEL_INVALID_DEFAULT"


def test_operator_change_keeps_api_keys_secret() -> None:
    provider = _catalogue().providers[0]
    payload = SharedModelsChange(
        expected_version=0,
        reason="统一模型配置联调",
        providers=[
            SharedProviderChange(
                **provider.model_dump(exclude={"encrypted_api_key"}),
                api_key=SecretStr("synthetic-private-key"),
            )
        ],
        defaults=_catalogue().defaults,
    )
    assert "synthetic-private-key" not in repr(payload)
    assert "synthetic-private-key" not in str(payload.model_dump())


def test_merchant_model_management_is_forbidden_after_activation() -> None:
    db = Mock()
    db.scalar.return_value = SystemSetup(
        is_installed=True,
        config={SHARED_KEY: _catalogue().model_dump(mode="json")},
    )
    with pytest.raises(HTTPException) as error:
        require_operator_model_management(db)
    assert error.value.status_code == 403


def test_shared_reconciliation_disables_legacy_merchant_provider() -> None:
    catalogue = _catalogue()
    project_id = uuid4()
    managed = [
        AIProvider(
            id=uuid4(), project_id=project_id,
            provider=template.provider, name=template.name,
            api_key=template.encrypted_api_key, models=[], is_active=True,
        )
        for template in catalogue.providers
    ]
    legacy = AIProvider(
        id=uuid4(), project_id=project_id, provider="legacy",
        name="legacy", api_key=encrypt_str("synthetic-old-key"),
        models=[], is_active=True,
    )
    db = Mock()
    db.scalars.return_value.all.return_value = [*managed, legacy]
    db.scalar.return_value = ProjectAIConfig(project_id=project_id)
    apply_shared_models_to_project(db, project_id, catalogue)
    assert legacy.is_active is False
    assert legacy.sync_status == "pending"
    assert all(provider.is_active for provider in managed)


@pytest.mark.asyncio
async def test_failed_provider_sync_keeps_default_config_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock
    from app.api.v1.endpoints import operations_shared_models

    provider = Mock(sync_status="pending")
    config = Mock(sync_status="pending")
    db = Mock()
    db.scalars.side_effect = [
        Mock(all=Mock(return_value=[provider])),
        Mock(all=Mock(return_value=[config])),
    ]
    sync_providers = AsyncMock(return_value=(False, "synthetic error", None))
    sync_configs = AsyncMock()
    monkeypatch.setattr(
        operations_shared_models, "sync_providers_with_retry", sync_providers
    )
    monkeypatch.setattr(
        operations_shared_models, "sync_configs_with_retry", sync_configs
    )
    await operations_shared_models._publish(db)
    assert provider.sync_status == "failed"
    assert config.sync_status == "pending"
    sync_configs.assert_not_called()


def test_private_runtime_lists_shared_media_without_exposing_media_keys() -> (
    None
):
    catalogue = _catalogue()
    chat = catalogue.providers[0]
    policy = StoredPlatformModel(
        version=1,
        definition=PlatformModelDefinition(
            model="chat-model",
            provider_kind="openai_compatible",
            api_base_url=chat.api_base_url,
        ),
        encrypted_api_key=chat.encrypted_api_key,
    )
    db = Mock()
    db.scalar.return_value = SystemSetup(
        is_installed=True,
        config={
            SHARED_KEY: catalogue.model_dump(mode="json"),
            "saas_platform_model": policy.model_dump(mode="json"),
        },
    )
    runtime = runtime_model(db)
    assert runtime is not None
    assert runtime.approved_media_models == {
        "asr": "asr-model",
        "ocr": "ocr-model",
        "vlm": "vlm-model",
    }
    assert "synthetic-media-key" not in runtime.model_dump_json()


@pytest.mark.asyncio
async def test_tenant_cannot_call_provider_list_after_activation() -> None:
    import httpx
    from fastapi import Depends, FastAPI

    from app.api.v1.endpoints.ai_providers import router as providers_router
    from app.core.database import get_db

    db = Mock()
    db.scalar.return_value = SystemSetup(
        is_installed=True,
        config={SHARED_KEY: _catalogue().model_dump(mode="json")},
    )
    app = FastAPI()
    app.include_router(
        providers_router,
        prefix="/v1/ai/providers",
        dependencies=[Depends(require_operator_model_management)],
    )
    app.dependency_overrides[get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/ai/providers")
    assert response.status_code == 403
    assert "API Key" not in response.text

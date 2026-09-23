"""Platform credentials stay encrypted in setup metadata and absent from operator responses."""

from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import SecretStr

from app.core.exceptions import TGOAPIException
from app.models import SystemSetup
from app.schemas.platform_models import PlatformModelChange
from app.services.platform_models import (
    read_model_policy,
    update_model_policy,
    runtime_model,
)


def test_policy_encrypts_secret_preserves_other_setup_settings_and_rejects_stale_version():
    db = Mock()
    setup = SystemSetup(is_installed=True, config={"unrelated": "preserved"})
    db.scalar.return_value = setup
    operator = Mock(id=uuid4())
    change = PlatformModelChange(
        expected_version=0,
        model="synthetic-model",
        provider_kind="openai",
        api_key=SecretStr("synthetic-model-key-only"),
        reason="合成平台模型设置",
    )
    result = update_model_policy(db, operator, change)
    assert result.version == 1 and result.has_api_key
    assert "synthetic-model-key-only" not in result.model_dump_json()
    assert "synthetic-model-key-only" not in str(setup.config)
    assert setup.config["unrelated"] == "preserved"
    assert runtime_model(db).api_key.get_secret_value() == "synthetic-model-key-only"
    assert "synthetic-model-key-only" not in repr(runtime_model(db))
    assert read_model_policy(db).version == 1
    with pytest.raises(TGOAPIException) as error:
        update_model_policy(db, operator, change)
    assert error.value.status_code == 409
    updated = update_model_policy(
        db,
        operator,
        change.model_copy(
            update={"expected_version": 1, "api_key": None, "model": "synthetic-new"}
        ),
    )
    assert updated.has_api_key and runtime_model(db).model == "synthetic-new"


def test_disabled_model_never_falls_back_to_customer_credentials():
    db = Mock()
    db.scalar.return_value = SystemSetup(is_installed=True, config={})
    update_model_policy(
        db,
        Mock(id=uuid4()),
        PlatformModelChange(
            expected_version=0,
            model="synthetic-model",
            provider_kind="openai",
            active=False,
            reason="合成暂停模型服务",
        ),
    )
    with pytest.raises(TGOAPIException) as error:
        runtime_model(db)
    assert error.value.code == "PLATFORM_MODEL_DISABLED"


@pytest.mark.asyncio
async def test_invalid_model_configuration_does_not_echo_key():
    import httpx
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from app.api.v1.endpoints.operations_tasks import save_model_policy
    from app.api.v1.endpoints.operations import require_operator
    from app.core.database import get_db
    from app.core.exceptions import validation_exception_handler

    app = FastAPI()
    app.add_api_route("/v1/ops/model-policy", save_model_policy, methods=["PUT"])
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.dependency_overrides[require_operator] = lambda: Mock(id=uuid4())
    app.dependency_overrides[get_db] = lambda: Mock()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.put(
            "/v1/ops/model-policy",
            json={
                "expected_version": 0,
                "model": "synthetic",
                "provider_kind": "openai",
                "api_key": {"invalid": "synthetic-never-echo-key"},
                "reason": "合成配置校验测试",
            },
        )
    assert response.status_code == 422
    assert "synthetic-never-echo-key" not in response.text

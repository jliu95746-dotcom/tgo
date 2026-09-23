"""Operator-controlled default model; encrypted storage uses existing setup metadata."""

from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import SecretStr

from app.core.exceptions import TGOAPIException
from app.models import SystemSetup
from app.models.billing import BillingAudit
from app.models.platform_operator import PlatformOperator
from app.schemas.platform_models import (
    PlatformModelChange,
    PlatformModelDefinition,
    PlatformModelPolicy,
    PlatformModelRuntime,
    StoredPlatformModel,
)
from app.utils.crypto import decrypt_str, encrypt_str

POLICY_KEY = "saas_platform_model"


def stored_policy(
    db: Session, *, lock: bool = False
) -> tuple[SystemSetup | None, StoredPlatformModel | None]:
    statement = (
        select(SystemSetup).order_by(SystemSetup.created_at, SystemSetup.id).limit(1)
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    setup = db.scalar(statement)
    value = setup.config.get(POLICY_KEY) if setup and setup.config else None
    return (
        setup,
        StoredPlatformModel.model_validate(value) if value is not None else None,
    )


def read_model_policy(db: Session) -> PlatformModelPolicy:
    _, stored = stored_policy(db)
    return PlatformModelPolicy(
        version=stored.version if stored else 0,
        definition=stored.definition if stored else None,
        has_api_key=bool(stored and stored.encrypted_api_key),
    )


def update_model_policy(
    db: Session, operator: PlatformOperator, payload: PlatformModelChange
) -> PlatformModelPolicy:
    setup, previous = stored_policy(db, lock=True)
    if setup is None or not setup.is_installed:
        raise TGOAPIException("请先完成系统初始化", code="SETUP_REQUIRED", status_code=409)
    version = previous.version if previous else 0
    if payload.expected_version != version:
        raise TGOAPIException(
            "模型配置已被修改，请刷新后重试", code="MODEL_POLICY_CONFLICT", status_code=409
        )
    if payload.provider_kind == "openai_compatible" and not payload.api_base_url:
        raise TGOAPIException(
            "兼容接口需要填写模型 API 地址", code="MODEL_ENDPOINT_REQUIRED", status_code=422
        )
    key = payload.api_key.get_secret_value().strip() if payload.api_key else None
    if key is not None and (len(key) < 8 or len(key) > 4096):
        raise TGOAPIException("模型凭据格式无效", code="MODEL_KEY_INVALID", status_code=422)
    encrypted = (
        encrypt_str(key) if key else previous.encrypted_api_key if previous else ""
    )
    if payload.active and not encrypted:
        raise TGOAPIException(
            "启用平台模型前请填写 API Key", code="MODEL_KEY_REQUIRED", status_code=422
        )
    definition = PlatformModelDefinition.model_validate(
        payload.model_dump(exclude={"expected_version", "api_key", "reason"})
    )
    stored = StoredPlatformModel(
        version=version + 1, definition=definition, encrypted_api_key=encrypted
    )
    setup.config = {**(setup.config or {}), POLICY_KEY: stored.model_dump(mode="json")}
    db.add(
        BillingAudit(
            operator_id=operator.id,
            action="platform_model_change",
            reason=payload.reason,
            detail={
                "version": stored.version,
                "model": definition.model,
                "active": definition.active,
                "key_changed": key is not None,
            },
        )
    )
    db.flush()
    return PlatformModelPolicy(
        version=stored.version, definition=definition, has_api_key=bool(encrypted)
    )


def runtime_model(db: Session) -> PlatformModelRuntime | None:
    _, stored = stored_policy(db)
    if stored is None:
        return None
    if not stored.definition.active:
        raise TGOAPIException(
            "平台模型服务已暂停", code="PLATFORM_MODEL_DISABLED", status_code=503
        )
    key = decrypt_str(stored.encrypted_api_key)
    if not key:
        raise TGOAPIException(
            "平台模型凭据暂不可用", code="MODEL_KEY_UNAVAILABLE", status_code=503
        )
    return PlatformModelRuntime(
        **stored.definition.model_dump(), api_key=SecretStr(key)
    )

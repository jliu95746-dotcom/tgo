"""Reconcile one operator-owned model catalogue into tenant runtime records."""

from urllib.parse import urlsplit
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models import (
    AIModel,
    AIProvider,
    Project,
    ProjectAIConfig,
    SystemSetup,
)
from app.models.billing import BillingAudit
from app.models.platform_operator import PlatformOperator
from app.schemas.platform_models import (
    PlatformModelDefinition,
    StoredPlatformModel,
)
from app.schemas.shared_models import (
    ModelPurpose,
    SharedModel,
    SharedModelsChange,
    SharedModelsView,
    SharedProviderView,
    SharedSelection,
    StoredSharedModels,
    StoredSharedProvider,
    SharedModelsImport,
)
from app.services.ai_provider_sync import _map_kind_and_vendor
from app.services.platform_models import POLICY_KEY
from app.utils.crypto import encrypt_str

SHARED_KEY = "shared_ai_models"
PURPOSES = ("chat", "embedding", "asr", "ocr", "vlm")


def _setup(db: Session, lock: bool = False) -> SystemSetup | None:
    query = (
        select(SystemSetup)
        .order_by(SystemSetup.created_at, SystemSetup.id)
        .limit(1)
    )
    if lock:
        query = query.with_for_update().execution_options(
            populate_existing=True
        )
    return db.scalar(query)


def stored_shared_models(db: Session) -> StoredSharedModels | None:
    setup = _setup(db)
    value = setup.config.get(SHARED_KEY) if setup and setup.config else None
    return StoredSharedModels.model_validate(value) if value else None


def shared_models_active(db: Session) -> bool:
    return stored_shared_models(db) is not None


def shared_models_view(db: Session) -> SharedModelsView:
    stored = stored_shared_models(db)
    if stored is None:
        return SharedModelsView(
            version=0, enabled=False, synchronization="inactive",
            providers=[], defaults={}
        )
    provider_failed = db.scalar(
        select(AIProvider.id)
        .where(
            AIProvider.deleted_at.is_(None),
            AIProvider.sync_status == "failed",
        )
        .limit(1)
    ) is not None
    config_failed = db.scalar(
        select(ProjectAIConfig.id)
        .where(
            ProjectAIConfig.deleted_at.is_(None),
            ProjectAIConfig.sync_status == "failed",
        )
        .limit(1)
    ) is not None
    provider_pending = db.scalar(
        select(AIProvider.id)
        .where(
            AIProvider.deleted_at.is_(None),
            AIProvider.sync_status == "pending",
        )
        .limit(1)
    ) is not None
    config_pending = db.scalar(
        select(ProjectAIConfig.id)
        .where(
            ProjectAIConfig.deleted_at.is_(None),
            ProjectAIConfig.sync_status == "pending",
        )
        .limit(1)
    ) is not None
    return SharedModelsView(
        version=stored.version,
        enabled=True,
        synchronization=(
            "failed" if provider_failed or config_failed
            else "pending" if provider_pending or config_pending
            else "synced"
        ),
        providers=[
            SharedProviderView(
                **provider.model_dump(exclude={"encrypted_api_key"}),
                has_api_key=bool(provider.encrypted_api_key),
            )
            for provider in stored.providers
        ],
        defaults=stored.defaults,
    )


def _validate(stored: StoredSharedModels) -> None:
    by_id = {provider.id: provider for provider in stored.providers}
    if len(by_id) != len(stored.providers):
        raise TGOAPIException(
            "模型服务 ID 不可重复", code="SHARED_MODEL_DUPLICATE", status_code=422
        )
    if set(stored.defaults) != set(PURPOSES):
        raise TGOAPIException(
            "请配置全部五类默认模型", code="SHARED_MODEL_DEFAULTS", status_code=422
        )
    for provider in stored.providers:
        if not provider.encrypted_api_key:
            raise TGOAPIException(
                "模型服务缺少 API Key", code="SHARED_MODEL_KEY", status_code=422
            )
        if provider.api_base_url:
            url = urlsplit(provider.api_base_url)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or (
                    url.scheme == "http"
                    and url.hostname not in {"localhost", "127.0.0.1", "::1"}
                )
            ):
                raise TGOAPIException(
                    "模型服务地址必须为 HTTPS 且不含凭据",
                    code="SHARED_MODEL_URL",
                    status_code=422,
                )
        identities = [model.model_id for model in provider.models]
        if provider.default_model and provider.default_model not in {
            model.model_id for model in provider.models
        }:
            raise TGOAPIException(
                "服务默认模型不存在", code="SHARED_MODEL_INVALID_DEFAULT",
                status_code=422,
            )
        if len(set(identities)) != len(identities):
            raise TGOAPIException(
                "模型名称与用途不可重复", code="SHARED_MODEL_DUPLICATE", status_code=422
            )
    for purpose, selection in stored.defaults.items():
        selected_provider = by_id.get(selection.provider_id)
        if (
            selected_provider is None
            or not selected_provider.is_active
            or not any(
                model.model_id == selection.model_id
                and model.model_type == purpose
                for model in selected_provider.models
            )
        ):
            raise TGOAPIException(
                "默认模型未指向可用的同类模型",
                code="SHARED_MODEL_INVALID_DEFAULT",
                status_code=422,
            )


def _sync_platform_chat_policy(
    setup: SystemSetup, stored: StoredSharedModels
) -> None:
    chat = stored.defaults["chat"]
    provider = next(
        item for item in stored.providers if item.id == chat.provider_id
    )
    kind, vendor = _map_kind_and_vendor(provider.provider, provider.config)
    old = (
        StoredPlatformModel.model_validate(setup.config[POLICY_KEY])
        if setup.config and POLICY_KEY in setup.config
        else None
    )
    previous = old.definition if old else None
    definition = PlatformModelDefinition(
        model=chat.model_id,
        provider_kind=cast(
            Literal["openai", "openai_compatible", "anthropic", "google"],
            kind,
        ),
        vendor=vendor,
        api_base_url=provider.api_base_url,
        active=True,
        input_fen_per_million=previous.input_fen_per_million
        if previous
        else None,
        output_fen_per_million=previous.output_fen_per_million
        if previous
        else None,
    )
    setup.config = {
        **(setup.config or {}),
        POLICY_KEY: StoredPlatformModel(
            version=(old.version if old else 0) + 1,
            definition=definition,
            encrypted_api_key=provider.encrypted_api_key,
        ).model_dump(mode="json"),
    }


def apply_shared_models_to_project(
    db: Session,
    project_id: UUID,
    stored: StoredSharedModels | None = None,
) -> list[AIProvider]:
    """Idempotent DB-only reconciliation; background jobs publish to AI/RAG."""
    stored = stored or stored_shared_models(db)
    if stored is None:
        return []
    existing = list(
        db.scalars(
            select(AIProvider).where(
                AIProvider.project_id == project_id,
                AIProvider.deleted_at.is_(None),
            )
        ).all()
    )
    mapped: dict[UUID, AIProvider] = {}
    for template in stored.providers:
        marker = str(template.id)
        target = next(
            (
                item
                for item in existing
                if item.id == template.id
                or (item.config or {}).get("_shared_template_id") == marker
            ),
            None,
        )
        if target is None:
            target = next(
                (
                    item
                    for item in existing
                    if item.provider == template.provider
                    and item.name == template.name
                    and item not in mapped.values()
                ),
                None,
            )
        if target is None:
            target = AIProvider(
                project_id=project_id,
                provider=template.provider,
                name=template.name,
                api_key=template.encrypted_api_key,
            )
            db.add(target)
            db.flush()
            existing.append(target)
        target.provider = template.provider
        target.name = template.name
        target.api_key = template.encrypted_api_key
        target.api_base_url = template.api_base_url
        target.default_model = template.default_model
        target.config = {
            **(template.config or {}),
            "_shared_template_id": marker,
        }
        target.is_active = template.is_active
        target.sync_status = "pending"
        db.flush()
        current_models = {
            model.model_id: model
            for model in target.models
            if model.deleted_at is None
        }
        configured_model_ids = {model.model_id for model in template.models}
        for model_id, model in current_models.items():
            if model_id not in configured_model_ids:
                model.is_active = False
        for configured in template.models:
            model = current_models.get(configured.model_id)
            if model is None:
                model = AIModel(
                    provider_id=target.id,
                    provider=target.provider,
                    model_id=configured.model_id,
                    model_name=configured.model_id,
                )
                db.add(model)
            model.model_type = configured.model_type
            model.capabilities = configured.capabilities
            model.is_active = True
        mapped[template.id] = target
    managed_ids = {provider.id for provider in mapped.values()}
    for item in existing:
        if item.id not in managed_ids and item.is_active:
            item.is_active = False
            item.sync_status = "pending"
    cfg = db.scalar(
        select(ProjectAIConfig).where(
            ProjectAIConfig.project_id == project_id,
            ProjectAIConfig.deleted_at.is_(None),
        )
    )
    if cfg is None:
        cfg = ProjectAIConfig(project_id=project_id)
        db.add(cfg)
    for purpose, selection in stored.defaults.items():
        setattr(
            cfg,
            f"default_{purpose}_provider_id",
            mapped[selection.provider_id].id,
        )
        setattr(cfg, f"default_{purpose}_model", selection.model_id)
    cfg.sync_status = "pending"
    db.flush()
    return list(mapped.values())


def _save(
    db: Session,
    operator: PlatformOperator,
    stored: StoredSharedModels,
    reason: str,
) -> SharedModelsView:
    _validate(stored)
    setup = _setup(db, lock=True)
    if setup is None or not setup.is_installed:
        raise TGOAPIException(
            "请先完成系统初始化", code="SETUP_REQUIRED", status_code=409
        )
    current = (
        StoredSharedModels.model_validate(setup.config[SHARED_KEY])
        if setup.config and SHARED_KEY in setup.config
        else None
    )
    if stored.version != (current.version if current else 0) + 1:
        raise TGOAPIException(
            "模型配置已更新，请刷新后重试", code="SHARED_MODEL_CONFLICT", status_code=409
        )
    setup.config = {
        **(setup.config or {}),
        SHARED_KEY: stored.model_dump(mode="json"),
    }
    _sync_platform_chat_policy(setup, stored)
    for project_id in db.scalars(
        select(Project.id).where(Project.deleted_at.is_(None))
    ):
        apply_shared_models_to_project(db, project_id, stored)
    db.add(
        BillingAudit(
            operator_id=operator.id,
            action="shared_models_change",
            reason=reason,
            detail={
                "version": stored.version,
                "providers": len(stored.providers),
            },
        )
    )
    db.flush()
    return shared_models_view(db)


def import_shared_models(
    db: Session,
    operator: PlatformOperator,
    payload: SharedModelsImport,
) -> SharedModelsView:
    current = stored_shared_models(db)
    if payload.expected_version != (current.version if current else 0):
        raise TGOAPIException(
            "模型配置已更新，请刷新后重试", code="SHARED_MODEL_CONFLICT", status_code=409
        )
    providers = db.scalars(
        select(AIProvider).where(
            AIProvider.project_id == payload.source_project_id,
            AIProvider.deleted_at.is_(None),
            AIProvider.is_active.is_(True),
        )
    ).all()
    cfg = db.scalar(
        select(ProjectAIConfig).where(
            ProjectAIConfig.project_id == payload.source_project_id,
            ProjectAIConfig.deleted_at.is_(None),
        )
    )
    if not providers or cfg is None:
        raise TGOAPIException(
            "源商家的模型配置不完整", code="SHARED_MODEL_SOURCE", status_code=422
        )
    by_id = {provider.id: provider for provider in providers}
    defaults: dict[ModelPurpose, SharedSelection] = {}
    for purpose in PURPOSES:
        provider_id = getattr(cfg, f"default_{purpose}_provider_id")
        model_id = getattr(cfg, f"default_{purpose}_model")
        if provider_id not in by_id or not model_id:
            raise TGOAPIException(
                "源商家的五类默认模型未配置齐全", code="SHARED_MODEL_SOURCE", status_code=422
            )
        defaults[cast(ModelPurpose, purpose)] = SharedSelection(
            provider_id=provider_id, model_id=model_id
        )
    stored = StoredSharedModels(
        version=payload.expected_version + 1,
        defaults=defaults,
        providers=[
            StoredSharedProvider(
                id=item.id,
                provider=item.provider,
                name=item.name,
                api_base_url=item.api_base_url,
                config={
                    key: value
                    for key, value in (item.config or {}).items()
                    if key != "_shared_template_id"
                },
                default_model=item.default_model,
                is_active=item.is_active,
                encrypted_api_key=item.api_key,
                models=[
                    SharedModel(
                        model_id=model.model_id,
                        model_type=cast(ModelPurpose, model.model_type),
                        capabilities=model.capabilities,
                    )
                    for model in item.models
                    if model.deleted_at is None
                ],
            )
            for item in providers
        ],
    )
    return _save(db, operator, stored, payload.reason)


def change_shared_models(
    db: Session,
    operator: PlatformOperator,
    payload: SharedModelsChange,
) -> SharedModelsView:
    current = stored_shared_models(db)
    if payload.expected_version != (current.version if current else 0):
        raise TGOAPIException(
            "模型配置已更新，请刷新后重试", code="SHARED_MODEL_CONFLICT", status_code=409
        )
    previous = (
        {provider.id: provider for provider in current.providers}
        if current
        else {}
    )
    providers = []
    for item in payload.providers:
        value = (
            item.api_key.get_secret_value().strip() if item.api_key else None
        )
        encrypted = (
            encrypt_str(value)
            if value
            else (
                previous[item.id].encrypted_api_key
                if item.id in previous
                else ""
            )
        )
        providers.append(
            StoredSharedProvider(
                **item.model_dump(exclude={"api_key"}),
                encrypted_api_key=encrypted,
            )
        )
    stored = StoredSharedModels(
        version=payload.expected_version + 1,
        providers=providers,
        defaults=payload.defaults,
    )
    return _save(db, operator, stored, payload.reason)

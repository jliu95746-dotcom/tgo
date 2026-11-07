"Read current model ownership and synchronization without exporting \
credentials."

import hashlib
import hmac
import json
from typing import Literal

from pydantic import JsonValue, TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_model import AIModel
from app.models.ai_provider import AIProvider
from app.models.project_ai_config import ProjectAIConfig
from app.schemas.agent_activation_models import (
    ActivationModelRuntime,
    AgentActivationModel,
    AgentActivationModelQuery,
)
from app.services.agent_activation_requirements import require_activation_actor
from app.services.ai_provider_sync import _provider_to_upsert
from app.services.platform_models import read_model_policy, runtime_model

json_object = TypeAdapter(dict[str, JsonValue])


def _revision(value: dict[str, JsonValue]) -> str:
    raw = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hmac.new(
        settings.SECRET_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()


def current_model(
    db: Session, query: AgentActivationModelQuery
) -> AgentActivationModel:
    require_activation_actor(db, query.project_id, query.requested_by)

    def unready(
        status: Literal["missing", "disabled", "not_synced", "unavailable"]
    ) -> AgentActivationModel:
        return AgentActivationModel(**query.model_dump(), status=status)

    if settings.SAAS_ENABLED and settings.SAAS_BILLING_ENABLED:
        try:
            platform = runtime_model(db)
            if (
                platform is None
                or query.provider_id is not None
                or query.model
                not in {
                    "__system_default__",
                    platform.model,
                }
            ):
                return unready("missing")
            runtime = ActivationModelRuntime(
                model=platform.model,
                provider_kind=platform.provider_kind,
                vendor=platform.vendor,
                api_key=platform.api_key,
                api_base_url=platform.api_base_url,
                timeout=60,
            )
            runtime_revision = runtime.private_revision(settings.SECRET_KEY)
            return AgentActivationModel(
                **query.model_dump(),
                status="ready",
                source="platform",
                effective_model=platform.model,
                runtime_revision=runtime_revision,
                owner_revision=_revision(
                    {
                        "runtime_revision": runtime_revision,
                        "policy_version": read_model_policy(db).version,
                    }
                ),
            )
        except Exception:
            return unready("unavailable")

    provider_id, model_id = query.provider_id, query.model
    config = None
    if query.model == "__system_default__":
        config = db.scalar(
            select(ProjectAIConfig)
            .where(
                ProjectAIConfig.project_id == query.project_id,
                ProjectAIConfig.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        if (
            config is None
            or not config.default_chat_provider_id
            or not config.default_chat_model
        ):
            return unready("missing")
        if config.sync_status != "synced":
            return unready("not_synced")
        provider_id, model_id = (
            config.default_chat_provider_id,
            config.default_chat_model,
        )
    if provider_id is None:
        return unready("missing")
    provider = db.get(AIProvider, provider_id, populate_existing=True)
    if (
        provider is None
        or provider.project_id != query.project_id
        or provider.deleted_at is not None
    ):
        return unready("missing")
    if not provider.is_active:
        return unready("disabled")
    if provider.sync_status != "synced":
        return unready("not_synced")
    model = db.scalar(
        select(AIModel)
        .where(
            AIModel.provider_id == provider_id,
            AIModel.model_id == model_id,
            AIModel.model_type == "chat",
            AIModel.deleted_at.is_(None),
        )
        .execution_options(populate_existing=True)
    )
    if model is None:
        return unready("missing")
    if not model.is_active:
        return unready("disabled")
    try:
        synced = _provider_to_upsert(provider)
        if not synced.get("api_key"):
            return unready("missing")
        runtime = ActivationModelRuntime.model_validate(
            {
                "model": model_id,
                **{
                    field: synced.get(field)
                    for field in ActivationModelRuntime.model_fields
                    if field != "model"
                },
            }
        )
        runtime_revision = runtime.private_revision(settings.SECRET_KEY)
        definition = json_object.validate_python(
            {
                "runtime_revision": runtime_revision,
                "provider_id": str(provider_id),
                "provider_updated_at": provider.updated_at.isoformat()
                if provider.updated_at
                else None,
                "model_id": str(model.id),
                "model_updated_at": model.updated_at.isoformat()
                if model.updated_at
                else None,
                "capabilities": model.capabilities or {},
                "context_window": model.context_window,
                "max_tokens": model.max_tokens,
                "default_updated_at": config.updated_at.isoformat()
                if config is not None and config.updated_at
                else None,
            }
        )
        return AgentActivationModel(
            **query.model_dump(),
            status="ready",
            effective_model=model_id,
            runtime_revision=runtime_revision,
            owner_revision=_revision(definition),
        )
    except Exception:
        return unready("unavailable")

"""Default model seed resolution for AI provider creation flows."""

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIProviderDefaultModel

OPENAI_COMPATIBLE_PROVIDER = "openai_compatible"

_PROVIDER_ALIASES: dict[str, str] = {
    "azure": "azure_openai",
    "azure-openai": "azure_openai",
    "qwen": "dashscope",
    "ali": "dashscope",
    "aliyun": "dashscope",
    "custom": OPENAI_COMPATIBLE_PROVIDER,
}


@dataclass(frozen=True)
class ProviderModelSeed:
    """Model seed used to initialize AIModel rows for a new provider."""

    model_id: str
    model_name: str
    model_type: str


def normalize_provider_key(provider: str) -> str:
    """Normalize provider aliases to the canonical key used by default templates."""
    raw = (provider or "").strip().lower()
    if not raw:
        return OPENAI_COMPATIBLE_PROVIDER
    return _PROVIDER_ALIASES.get(raw, raw)


def resolve_initial_model_seeds(
    db: Session,
    provider: str,
    requested_models: list[str] | None,
) -> list[ProviderModelSeed]:
    """Resolve the model list used at provider creation time."""
    if requested_models:
        return _seeds_from_requested_models(requested_models)
    return get_default_model_seeds(db, provider)


def get_default_model_seeds(
    db: Session, provider: str
) -> list[ProviderModelSeed]:
    """Load default model seeds from DB for a provider, with openai-compatible fallback."""
    provider_key = normalize_provider_key(provider)
    rows = _load_default_rows(db, provider_key)
    if not rows and provider_key != OPENAI_COMPATIBLE_PROVIDER:
        rows = _load_default_rows(db, OPENAI_COMPATIBLE_PROVIDER)
    return [
        ProviderModelSeed(
            model_id=row.model_id,
            model_name=row.model_name or row.model_id,
            model_type=row.model_type or infer_model_type(row.model_id),
        )
        for row in rows
    ]


def infer_model_type(model_id: str) -> str:
    return "embedding" if "embedding" in model_id.lower() else "chat"


def _seeds_from_requested_models(
    models: Iterable[str],
) -> list[ProviderModelSeed]:
    ordered_seeds: list[ProviderModelSeed] = []
    seen_model_ids: set[str] = set()

    for raw_model_id in models:
        model_id = raw_model_id.strip()
        if not model_id or model_id in seen_model_ids:
            continue
        seen_model_ids.add(model_id)
        ordered_seeds.append(
            ProviderModelSeed(
                model_id=model_id,
                model_name=model_id,
                model_type=infer_model_type(model_id),
            )
        )
    return ordered_seeds


def _load_default_rows(
    db: Session, provider: str
) -> list[AIProviderDefaultModel]:
    stmt = (
        select(AIProviderDefaultModel)
        .where(
            AIProviderDefaultModel.provider == provider,
            AIProviderDefaultModel.is_active.is_(True),
        )
        .order_by(
            AIProviderDefaultModel.sort_order.asc(),
            AIProviderDefaultModel.model_id.asc(),
        )
    )
    return list(db.scalars(stmt).all())

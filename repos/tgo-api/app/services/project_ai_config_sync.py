from __future__ import annotations

from typing import Any, Optional, Sequence
from datetime import datetime

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.project_ai_config import ProjectAIConfig

logger = get_logger("services.project_ai_config_sync")


def _config_to_upsert(item: ProjectAIConfig) -> dict[str, Any]:
    upsert: dict[str, Any] = {
        "id": str(item.id),  # include config id for traceability
        "project_id": str(item.project_id),  # primary key on AI service side
        "default_chat_provider_id": str(item.default_chat_provider_id) if item.default_chat_provider_id else None,
        "default_chat_model": item.default_chat_model,
        "default_embedding_provider_id": str(item.default_embedding_provider_id) if item.default_embedding_provider_id else None,
        "default_embedding_model": item.default_embedding_model,
        "default_asr_provider_id": str(item.default_asr_provider_id) if item.default_asr_provider_id else None,
        "default_asr_model": item.default_asr_model,
        "default_ocr_provider_id": str(item.default_ocr_provider_id) if item.default_ocr_provider_id else None,
        "default_ocr_model": item.default_ocr_model,
        "default_vlm_provider_id": str(item.default_vlm_provider_id) if item.default_vlm_provider_id else None,
        "default_vlm_model": item.default_vlm_model,
    }
    return {k: v for k, v in upsert.items() if v is not None}


def _build_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.AI_SERVICE_API_KEY:
        headers["X-API-Key"] = settings.AI_SERVICE_API_KEY
    return headers


async def sync_configs(items: Sequence[ProjectAIConfig]) -> tuple[bool, Optional[str], Optional[dict]]:
    url = f"{settings.AI_SERVICE_URL.rstrip('/')}/api/v1/project-ai-configs/sync"
    payload = {"configs": [_config_to_upsert(x) for x in items]}
    try:
        async with httpx.AsyncClient(timeout=settings.AI_SERVICE_TIMEOUT) as client:
            resp = await client.post(url, json=payload, headers=_build_headers())
        if resp.status_code >= 400:
            return False, f"{resp.status_code} {resp.text}", None
        try:
            data = resp.json()
        except Exception:
            data = None
        return True, None, data
    except httpx.TimeoutException as te:
        return False, f"timeout: {te}", None
    except httpx.RequestError as re:
        return False, f"request_error: {re}", None
    except Exception as e:  # pragma: no cover
        return False, f"unexpected_error: {e}", None


async def sync_config(item: ProjectAIConfig) -> tuple[bool, Optional[str], Optional[dict]]:
    return await sync_configs([item])


# Retry helpers
async def sync_config_with_retry(
    item: ProjectAIConfig,
    max_retries: Optional[int] = None,
    initial_delay: Optional[int] = None,
) -> tuple[bool, Optional[str], Optional[dict]]:
    """Sync a single ProjectAIConfig with exponential backoff retry.

    Returns (ok, err_msg, response_data).
    """
    import asyncio

    retries = (
        settings.PROJECT_AI_CONFIG_SYNC_RETRY_COUNT if max_retries is None else max_retries
    )
    base_delay = (
        settings.PROJECT_AI_CONFIG_SYNC_RETRY_DELAY if initial_delay is None else initial_delay
    )

    ok, err, data = await sync_config(item)
    if ok:
        return ok, err, data

    last_err = err
    for attempt in range(1, max(0, retries) + 1):
        delay = max(0, base_delay) * (2 ** (attempt - 1))
        logger.warning(
            "ProjectAIConfig sync failed; retrying",
            extra={
                "project_id": str(item.project_id),
                "attempt": attempt,
                "max_retries": retries,
                "delay_sec": delay,
                "error": str(last_err),
            },
        )
        await asyncio.sleep(delay)
        ok, err, data = await sync_config(item)
        if ok:
            return ok, err, data
        last_err = err

    return False, last_err, None


async def sync_configs_with_retry(
    items: Sequence[ProjectAIConfig],
    max_retries: Optional[int] = None,
    initial_delay: Optional[int] = None,
) -> tuple[bool, Optional[str], Optional[dict]]:
    import asyncio

    retries = (
        settings.PROJECT_AI_CONFIG_SYNC_RETRY_COUNT if max_retries is None else max_retries
    )
    base_delay = (
        settings.PROJECT_AI_CONFIG_SYNC_RETRY_DELAY if initial_delay is None else initial_delay
    )

    ok, err, data = await sync_configs(items)
    if ok:
        return ok, err, data

    last_err = err
    for attempt in range(1, max(0, retries) + 1):
        delay = max(0, base_delay) * (2 ** (attempt - 1))
        logger.warning(
            "ProjectAIConfig batch sync failed; retrying",
            extra={
                "count": len(items),
                "attempt": attempt,
                "max_retries": retries,
                "delay_sec": delay,
                "error": str(last_err),
            },
        )
        await asyncio.sleep(delay)
        ok, err, data = await sync_configs(items)
        if ok:
            return ok, err, data
        last_err = err

    return False, last_err, None


async def sync_config_with_retry_and_update(db, item: ProjectAIConfig) -> tuple[bool, Optional[str]]:
    """Sync one config with retries and update its sync fields in DB.

    Returns (ok, err_msg).
    """
    from sqlalchemy.orm import Session  # local import to avoid header changes
    assert isinstance(db, Session)

    ok, err, _ = await sync_config_with_retry(item)
    item.last_synced_at = datetime.utcnow()
    if ok:
        item.sync_status = "synced"
        item.sync_error = None
    else:
        item.sync_status = "failed"
        item.sync_error = str(err) if err else "unknown error"
    db.commit()
    try:
        db.refresh(item)
    except Exception:
        pass
    return ok, err


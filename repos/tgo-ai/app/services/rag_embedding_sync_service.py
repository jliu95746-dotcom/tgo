"""Embedding configuration sync from tgo-ai to tgo-rag with retry.

This service builds payloads from ProjectAIConfig + LLMProvider and dispatches
batch sync requests to the RAG service using app.services.rag_service.

Design notes:
- We build all payloads up-front using the current DB session.
- Network dispatch happens in a background task and does NOT reuse the request's DB session.
- Retries use exponential backoff (1s, 2s, 4s by default) and swallow errors with logging.
- Sync status fields on ProjectAIConfig are updated to reflect pending/success/failed and attempts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database import AsyncSessionLocal
from app.models.llm_provider import LLMProvider
from app.models.project_ai_config import ProjectAIConfig
from app.services.rag_service import (
    EmbeddingConfigCreate,
    EmbeddingConfigBatchSyncResponse,
    rag_service_client,
)


logger = get_logger("services.rag_embedding_sync")

RAG_EMBEDDING_DIMENSIONS = 1536
RAG_EMBEDDING_BATCH_SIZE = 10
_OPENAI_COMPATIBLE_KINDS = {
    "openai_compatible",
    "openai-compatible",
    "openai compatible",
}
_DASHSCOPE_VENDORS = {"ali", "aliyun", "dashscope", "qwen", "qwen3"}


def _map_provider_for_rag(provider_kind: str, vendor: Optional[str]) -> Optional[str]:
    """Map internal provider_kind/vendor to RAG "provider" enum.

    DashScope is synchronized by tgo-api as an OpenAI-compatible provider with
    ``vendor=dashscope``. RAG keeps a dedicated Qwen3 client so that model and
    dimension behavior remain explicit.
    """
    kind = (provider_kind or "").lower()
    vend = (vendor or "").lower() if vendor else None

    if kind == "openai":
        return "openai"
    if kind in _DASHSCOPE_VENDORS:
        return "qwen3"
    if kind in _OPENAI_COMPATIBLE_KINDS:
        if vend in _DASHSCOPE_VENDORS:
            return "qwen3"
        return "openai_compatible"

    return None


async def build_embedding_configs(
    db: AsyncSession,
    cfgs: Iterable[ProjectAIConfig],
) -> List[EmbeddingConfigCreate]:
    """Build EmbeddingConfigCreate payloads for the given ProjectAIConfig rows.

    Skips entries that don't have both embedding fields or lack a supported
    provider mapping.
    """
    payloads: List[EmbeddingConfigCreate] = []
    for cfg in cfgs:
        if not cfg.default_embedding_provider_id or not cfg.default_embedding_model:
            # Nothing to sync for this project
            continue

        # Fetch provider credentials by ID
        stmt = select(LLMProvider).where(LLMProvider.id == cfg.default_embedding_provider_id)
        res = await db.execute(stmt)
        provider: Optional[LLMProvider] = res.scalar_one_or_none()
        if not provider or not provider.is_active:
            logger.warning(
                "Embedding sync skipped: provider missing or inactive",
                project_id=str(cfg.project_id),
                provider_id=str(cfg.default_embedding_provider_id) if cfg.default_embedding_provider_id else None,
            )
            continue

        provider_name = _map_provider_for_rag(
            provider.provider_kind,
            provider.vendor,
        )
        if provider_name is None:
            logger.warning(
                "Embedding sync skipped: provider is not supported by RAG",
                project_id=str(cfg.project_id),
                provider_kind=provider.provider_kind,
                vendor=provider.vendor,
            )
            continue

        # Build payload
        payloads.append(
            EmbeddingConfigCreate(
                project_id=cfg.project_id,
                provider=provider_name,
                model=cfg.default_embedding_model,
                dimensions=RAG_EMBEDDING_DIMENSIONS,
                batch_size=RAG_EMBEDDING_BATCH_SIZE,
                api_key=provider.api_key,
                base_url=provider.api_base_url,
                is_active=True,
            )
        )

    return payloads


async def dispatch_to_rag_with_retry(
    configs: List[EmbeddingConfigCreate],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
) -> Optional[EmbeddingConfigBatchSyncResponse]:
    """Dispatch configs to RAG with bounded retries (exponential backoff).

    Returns the final response on success, else None after exhausting retries.
    Swallows exceptions and logs them to avoid crashing background tasks.
    """
    if not configs:
        return EmbeddingConfigBatchSyncResponse(success_count=0, failed_count=0, errors=[])

    # Network-only dispatch; used by background runner which manages DB updates
    attempt = 0
    while True:
        try:
            resp = await rag_service_client.batch_sync_embedding_configs(configs)
            logger.info(
                "Embedding configs synced to RAG",
                success_count=resp.success_count,
                failed_count=resp.failed_count,
            )
            return resp
        except Exception as e:  # noqa: BLE001 - intentional broad catch in background
            attempt += 1
            if attempt >= max_retries:
                logger.error(
                    "Embedding sync failed after retries",
                    error=str(e),
                    attempts=attempt,
                )
                return None
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "Embedding sync attempt failed; retrying",
                attempt=attempt,
                delay_seconds=delay,
                error=str(e),
            )
            await asyncio.sleep(delay)


def fire_and_forget_embedding_sync(configs: List[EmbeddingConfigCreate]) -> None:
    """Schedule background sync with retry; never raises to caller.

    Also updates ProjectAIConfig.sync_* fields to reflect lifecycle:
    - Mark as pending on start (do NOT reset attempt counter; it accumulates across cycles)
    - Increment attempts on each retry
    - Mark success with last_sync_at timestamp
    - Mark failed with last error after exhausting retries
    """

    async def _runner() -> None:
        from sqlalchemy import select
        project_ids = [c.project_id for c in configs]
        if not project_ids:
            return

        async with AsyncSessionLocal() as session:
            try:
                # Mark pending; clear last error but do not reset attempt counter
                res = await session.execute(select(ProjectAIConfig).where(ProjectAIConfig.project_id.in_(project_ids)))
                rows = res.scalars().all()
                for r in rows:
                    r.sync_status = "pending"
                    r.sync_error = None
                await session.commit()

                # Attempt loop with DB attempt count updates
                attempt = 0
                max_retries = 3
                base_delay = 1.0
                while True:
                    attempt += 1
                    # increment attempt count
                    res = await session.execute(select(ProjectAIConfig).where(ProjectAIConfig.project_id.in_(project_ids)))
                    rows = res.scalars().all()
                    for r in rows:
                        r.sync_attempt_count = (r.sync_attempt_count or 0) + 1
                    await session.commit()

                    try:
                        resp = await rag_service_client.batch_sync_embedding_configs(configs)
                        # Success: mark success with timestamp
                        now = datetime.now(timezone.utc)
                        res = await session.execute(select(ProjectAIConfig).where(ProjectAIConfig.project_id.in_(project_ids)))
                        rows = res.scalars().all()
                        for r in rows:
                            r.sync_status = "success"
                            r.sync_error = None
                            r.last_sync_at = now
                        await session.commit()
                        logger.info(
                            "Embedding configs synced to RAG",
                            success_count=getattr(resp, "success_count", None),
                            failed_count=getattr(resp, "failed_count", None),
                        )
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt >= max_retries:
                            # Final failure: mark failed with error
                            res = await session.execute(select(ProjectAIConfig).where(ProjectAIConfig.project_id.in_(project_ids)))
                            rows = res.scalars().all()
                            for r in rows:
                                r.sync_status = "failed"
                                r.sync_error = str(e)
                            await session.commit()
                            logger.error("Embedding sync failed after retries", error=str(e), attempts=attempt)
                            break
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning("Embedding sync attempt failed; retrying", attempt=attempt, delay_seconds=delay, error=str(e))
                        await asyncio.sleep(delay)
            except Exception as e:  # pragma: no cover - defensive
                # Ensure exceptions from background tasks are not left unhandled
                logger.error("Embedding sync background task crashed", error=str(e))

    # Schedule on current loop
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_runner())
    except RuntimeError:
        # No running loop (very rare in FastAPI context); run synchronously
        asyncio.run(_runner())


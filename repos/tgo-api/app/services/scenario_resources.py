"""Read resource owners and distinguish unavailable from missing."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import AIModel, AIProvider, ProjectAIConfig
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.scenario_resources import (
    KnowledgeAvailability,
    ResourceDetails,
    ResourceKind,
    ResourceSection,
    ScenarioResource,
    ScenarioResourceCatalog,
    ScopedDevicePage,
    ScopedToolRow,
    ScopedWorkflowPage,
)
from app.services import business_connections
from app.services.ai_client import ai_client
from app.services.device_control_client import device_control_client
from app.services.rag_client import rag_client
from app.services.workflow_client import workflow_client

logger = get_logger("scenario_resources")
CATALOG_TIMEOUT_SECONDS = 8


def model_catalog(db: Session, project_id: UUID) -> ResourceSection:
    rows = db.execute(
        select(AIModel, AIProvider)
        .join(
            AIProvider,
            AIModel.provider_id == AIProvider.id,
        )
        .where(
            AIProvider.project_id == project_id,
            AIProvider.deleted_at.is_(None),
            AIModel.deleted_at.is_(None),
        )
        .order_by(AIModel.model_name, AIModel.id)
    ).all()
    config = db.scalar(
        select(ProjectAIConfig).where(
            ProjectAIConfig.project_id == project_id,
            ProjectAIConfig.deleted_at.is_(None),
        )
    )
    items: list[ScenarioResource] = []
    for model, provider in rows:
        if provider.project_id != project_id:
            raise ValueError("Model scope mismatch")
        default_provider = getattr(
            config, f"default_{model.model_type}_provider_id", None
        )
        default_model = getattr(
            config, f"default_{model.model_type}_model", None
        )
        is_default = (
            default_provider == provider.id and default_model == model.model_id
        )
        synced = provider.sync_status == "synced" and (
            not is_default or getattr(config, "sync_status", None) == "synced"
        )
        items.append(
            ScenarioResource(
                id=str(model.id),
                kind="model",
                name=model.model_name,
                status="disabled"
                if not model.is_active or not provider.is_active
                else ("configured" if synced else "not_ready"),
                details=ResourceDetails(
                    provider_id=provider.id,
                    model_id=model.model_id,
                    purpose=model.model_type,
                    is_default=is_default,
                ),
            )
        )
    return ResourceSection(kind="model", items=items)


async def _knowledge_catalog(
    project: UUID, channel: KnowledgeChannel
) -> ResourceSection:
    data = KnowledgeAvailability.model_validate(
        await rag_client.knowledge_availability(str(project), channel.value)
    )
    if data.project_id != project or data.channel != channel:
        raise ValueError("Knowledge scope mismatch")
    return ResourceSection(
        kind="knowledge",
        items=[
            ScenarioResource(
                id=str(row.id),
                kind="knowledge",
                name=row.name,
                status="configured"
                if row.eligible_chunk_count > 0
                else "not_ready",
                details=ResourceDetails(
                    eligible_chunk_count=row.eligible_chunk_count,
                    total_chunk_count=row.total_chunk_count,
                    blocked_reasons=row.blocked_reasons,
                    resource_revision=row.resource_revision,
                ),
            )
            for row in data.collections
        ],
    )


async def _tool_catalog(project: UUID) -> ResourceSection:
    rows = TypeAdapter(list[ScopedToolRow]).validate_python(
        await ai_client.list_tools(project_id=str(project))
    )
    if any(row.project_id != project for row in rows):
        raise ValueError("Tool scope mismatch")
    return ResourceSection(
        kind="tool",
        items=[
            ScenarioResource(
                id=str(row.id),
                kind="tool",
                name=row.name,
                status="configured",
            )
            for row in rows
            if row.deleted_at is None
        ],
    )


async def _workflow_catalog(project: UUID) -> ResourceSection:
    page = ScopedWorkflowPage.model_validate(
        await workflow_client.list_workflows(
            project_id=str(project), limit=100
        )
    )
    if any(row.project_id != project for row in page.data):
        raise ValueError("Workflow scope mismatch")
    return ResourceSection(
        kind="workflow",
        status="partial" if page.pagination.has_next else "available",
        items=[
            ScenarioResource(
                id=row.id,
                kind="workflow",
                name=row.name,
                status="configured"
                if row.status == "active" and row.published_version is not None
                else "not_ready",
                details=ResourceDetails(workflow_version=row.published_version),
            )
            for row in page.data
        ],
    )


async def _device_catalog(project: UUID) -> ResourceSection:
    page = ScopedDevicePage.model_validate(
        await device_control_client.list_devices(
            project_id=str(project), limit=100
        )
    )
    if any(row.project_id != project for row in page.devices):
        raise ValueError("Device scope mismatch")
    return ResourceSection(
        kind="device",
        status="partial" if page.total > len(page.devices) else "available",
        items=[
            ScenarioResource(
                id=str(row.id),
                kind="device",
                name=row.device_name,
                status="configured" if row.status == "online" else "offline",
            )
            for row in page.devices
        ],
    )


async def _capture(
    kind: ResourceKind, operation: Callable[[], Awaitable[ResourceSection]]
) -> ResourceSection:
    try:
        return await asyncio.wait_for(
            operation(), timeout=CATALOG_TIMEOUT_SECONDS
        )
    except Exception as error:
        logger.warning(
            "Resource catalogue source failed: %s %s",
            kind,
            type(error).__name__,
        )
        return ResourceSection(
            kind=kind,
            status="unavailable",
            message="暂时无法读取此类资源，请重试。",
        )


def connection_catalog(db: Session, project_id: UUID) -> ResourceSection:
    rows = business_connections.list_connections(db, project_id, limit=101)
    return ResourceSection(kind="connection", status="partial" if len(rows) > 100 else "available", items=[
        ScenarioResource(id=str(row.id), kind="connection", name=row.name,
            status="unknown" if row.test_status == "unavailable" else "configured" if row.enabled and row.test_status == "passed"
                else "disabled" if row.test_status == "passed" and not row.enabled else "not_ready",
            details=ResourceDetails(purpose="order_logistics", resource_revision=row.resource_revision,
                blocked_reasons=["demo_only"] if row.mode == "demo" else ["connection_test_required"] if row.test_status != "passed" else []))
        for row in rows[:100]
    ])


async def load_catalog(
    db: Session,
    project_id: UUID,
    channel: KnowledgeChannel = KnowledgeChannel.WEB,
) -> ScenarioResourceCatalog:
    try:
        models = model_catalog(db, project_id)
    except Exception as error:
        logger.warning(
            "Resource catalogue source failed: model %s", type(error).__name__
        )
        models = ResourceSection(
            kind="model",
            status="unavailable",
            message="暂时无法读取模型配置，请重试。",
        )
    try:
        connections = connection_catalog(db, project_id)
    except Exception as error:
        db.rollback()
        logger.warning("Resource catalogue source failed: connection %s", type(error).__name__)
        connections = ResourceSection(kind="connection", status="unavailable", message="暂时无法读取企业业务连接，请重试。")
    sections = await asyncio.gather(
        _capture("knowledge", lambda: _knowledge_catalog(project_id, channel)),
        _capture("tool", lambda: _tool_catalog(project_id)),
        _capture("workflow", lambda: _workflow_catalog(project_id)),
        _capture("device", lambda: _device_catalog(project_id)),
    )
    return ScenarioResourceCatalog(
        project_id=project_id,
        channel=channel,
        checked_at=datetime.now(timezone.utc),
        sections=[
            models,
            *sections,
            connections,
        ],
    )

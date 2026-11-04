"""Versioned employee business questions with scoped, retry-safe writes."""
import asyncio
import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.agent_knowledge_catalog import AgentKnowledgeCatalog
from app.models.project import Project
from app.schemas.agent_knowledge_cases import (
    KnowledgeCatalog,
    KnowledgeCatalogWrite,
)
from app.schemas.agent_trial_reviews import KnowledgeReviewCase
from app.services.agent_activation_requirements import require_activation_actor
from app.services.ai_client import ai_client
from app.services.knowledge_review_cases import get_case, list_cases


def catalog_cases(
    identifier: UUID, questions: list[str], created_at: datetime | None = None
) -> list[KnowledgeReviewCase]:
    cases: list[KnowledgeReviewCase] = []
    for index, question in enumerate(questions):
        template: Literal["knowledge", "aftersales"] = (
            "knowledge" if index < 3 else "aftersales"
        )
        cases.append(
            KnowledgeReviewCase(
                id=f"kc.{identifier.hex}.{template}.{index % 3 + 1}",
                template_id=template,
                template_version=1,
                question=question,
                valid_from=created_at,
                expected_result="safe_refusal"
                if index % 3 == 2
                else "grounded_answer",
            )
        )
    return cases


def latest(
    db: Session, project: UUID, agent: UUID
) -> AgentKnowledgeCatalog | None:
    return db.scalar(
        select(AgentKnowledgeCatalog)
        .where(
            AgentKnowledgeCatalog.project_id == project,
            AgentKnowledgeCatalog.agent_id == agent,
        )
        .order_by(AgentKnowledgeCatalog.revision.desc())
        .limit(1)
    )


def current_cases(
    db: Session, project: UUID, agent: UUID
) -> list[KnowledgeReviewCase]:
    row = latest(db, project, agent)
    return (
        catalog_cases(row.id, row.questions, row.created_at)
        if row
        else list_cases()
    )


def response(
    agent: UUID, row: AgentKnowledgeCatalog | None, current: bool = True
) -> KnowledgeCatalog:
    if row is None:
        return KnowledgeCatalog(agent_id=agent, revision=0, cases=list_cases())
    return KnowledgeCatalog(
        agent_id=agent,
        id=row.id,
        revision=row.revision,
        request_key=row.request_key,
        configured_by=row.configured_by,
        created_at=row.created_at,
        current=current,
        cases=catalog_cases(row.id, row.questions, row.created_at),
    )


def _read(project: UUID, actor: UUID, agent: UUID) -> KnowledgeCatalog:
    with SessionLocal() as db:
        require_activation_actor(db, project, actor)
        return response(agent, latest(db, project, agent))


async def _agent(project: UUID, agent: UUID) -> None:
    value = await ai_client.get_agent(
        str(project), str(agent), include_tools=False
    )
    if str(value.get("id")) != str(agent) or str(
        value.get("project_id")
    ) != str(project):
        raise HTTPException(404, "未找到当前企业的 AI 员工")


async def read(project: UUID, actor: UUID, agent: UUID) -> KnowledgeCatalog:
    await _agent(project, agent)
    return await asyncio.to_thread(_read, project, actor, agent)


def save_record(
    db: Session,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: KnowledgeCatalogWrite,
) -> KnowledgeCatalog:
    # The stable parent row also serializes competing first writes.
    owned = db.scalar(
        select(Project)
        .where(Project.id == project, Project.deleted_at.is_(None))
        .with_for_update()
    )
    if owned is None:
        raise HTTPException(403, "当前企业不可配置")
    require_activation_actor(db, project, actor)
    digest = hashlib.sha256(
        json.dumps(
            {"agent": str(agent), **request.model_dump(mode="json")},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    previous = db.scalar(
        select(AgentKnowledgeCatalog).where(
            AgentKnowledgeCatalog.project_id == project,
            AgentKnowledgeCatalog.configured_by == actor,
            AgentKnowledgeCatalog.request_key == request.request_key,
        )
    )
    current = latest(db, project, agent)
    if previous:
        if previous.request_digest != digest:
            raise HTTPException(409, "该请求键已用于另一份问题配置，请重新读取")
        return response(
            agent, previous, current is not None and previous.id == current.id
        )
    if (current.revision if current else 0) != request.expected_revision:
        raise HTTPException(409, "测试问题已被修改，请重新读取后保存")
    row = AgentKnowledgeCatalog(
        id=uuid4(),
        project_id=project,
        agent_id=agent,
        revision=request.expected_revision + 1,
        configured_by=actor,
        request_key=request.request_key,
        request_digest=digest,
        questions=request.questions,
    )
    db.add(row)
    db.flush()
    return response(agent, row)


def _save(
    project: UUID, actor: UUID, agent: UUID, request: KnowledgeCatalogWrite
) -> KnowledgeCatalog:
    with SessionLocal.begin() as db:
        return save_record(db, project, actor, agent, request)


async def save(
    project: UUID, actor: UUID, agent: UUID, request: KnowledgeCatalogWrite
) -> KnowledgeCatalog:
    await _agent(project, agent)
    return await asyncio.to_thread(_save, project, actor, agent, request)


def _case(
    project: UUID, actor: UUID, agent: UUID, identifier: str
) -> KnowledgeReviewCase:
    with SessionLocal() as db:
        require_activation_actor(db, project, actor)
        if not identifier.startswith("kc."):
            return get_case(identifier)
        try:
            version = UUID(identifier.split(".")[1])
        except (ValueError, IndexError):
            raise HTTPException(404, "未找到测试问题版本") from None
        row = db.scalar(
            select(AgentKnowledgeCatalog).where(
                AgentKnowledgeCatalog.id == version,
                AgentKnowledgeCatalog.project_id == project,
                AgentKnowledgeCatalog.agent_id == agent,
            )
        )
        if row:
            for case in catalog_cases(row.id, row.questions, row.created_at):
                if case.id == identifier:
                    return case
        raise HTTPException(404, "未找到当前员工的测试问题版本")


async def case(
    project: UUID, actor: UUID, agent: UUID, identifier: str
) -> KnowledgeReviewCase:
    return await asyncio.to_thread(_case, project, actor, agent, identifier)

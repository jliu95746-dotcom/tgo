"""Scoped setup drafts with deduplication and revision checks."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import ConflictError, NotFoundError
from app.models.scenario_draft import ScenarioDraft
from app.schemas.scenario_drafts import (
    ScenarioBusiness,
    ScenarioDraftCreate,
    ScenarioDraftResponse,
    ScenarioDraftUpdate,
    ScenarioModule,
)
from app.services.scenario_templates import get_template


def _scope(
    project_id: UUID, staff_id: UUID
) -> tuple[ColumnElement[bool], ColumnElement[bool]]:
    return (
        ScenarioDraft.project_id == project_id,
        ScenarioDraft.created_by == staff_id,
    )


def _response(row: ScenarioDraft) -> ScenarioDraftResponse:
    return ScenarioDraftResponse.model_validate(row)


def get_draft(
    db: Session, project_id: UUID, staff_id: UUID, draft_id: UUID
) -> ScenarioDraftResponse:
    row = db.scalar(
        select(ScenarioDraft)
        .where(*_scope(project_id, staff_id), ScenarioDraft.id == draft_id)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("配置草稿")
    return _response(row)


def list_drafts(
    db: Session,
    project_id: UUID,
    staff_id: UUID,
    module: ScenarioModule | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ScenarioDraftResponse]:
    query = select(ScenarioDraft).where(*_scope(project_id, staff_id))
    if module is not None:
        query = query.where(ScenarioDraft.module == module)
    rows = db.scalars(
        query.order_by(ScenarioDraft.updated_at.desc(), ScenarioDraft.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [_response(row) for row in rows]


def create_draft(
    db: Session, project_id: UUID, staff_id: UUID, request: ScenarioDraftCreate
) -> ScenarioDraftResponse:
    digest = hashlib.sha256(
        json.dumps(
            request.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    query = select(ScenarioDraft).where(
        *_scope(project_id, staff_id),
        ScenarioDraft.idempotency_key == request.idempotency_key,
    )
    existing = db.scalar(query.execution_options(populate_existing=True))
    if existing is not None:
        if existing.request_digest != digest:
            raise ConflictError("此保存请求已用于不同的配置，请重新创建。")
        return _response(existing)
    template = get_template(
        request.module, request.template_id, request.template_version
    )
    business = ScenarioBusiness.model_validate(
        {
            **template.business_defaults.model_dump(),
            **request.business.model_dump(exclude_unset=True),
        }
    )
    row = ScenarioDraft(
        project_id=project_id,
        created_by=staff_id,
        idempotency_key=request.idempotency_key,
        request_digest=digest,
        template_id=template.id,
        module=template.module,
        template_version=template.version,
        channel=request.channel,
        business=business.model_dump(mode="json"),
        bindings=request.bindings.model_dump(mode="json"),
        agent_settings=request.agent_settings.model_dump(mode="json"),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(query)
        if existing is None or existing.request_digest != digest:
            raise ConflictError("保存请求发生冲突，请重新读取草稿。") from None
        return _response(existing)
    db.refresh(row)
    return _response(row)


def update_draft(
    db: Session,
    project_id: UUID,
    staff_id: UUID,
    draft_id: UUID,
    request: ScenarioDraftUpdate,
) -> ScenarioDraftResponse:
    current = get_draft(db, project_id, staff_id, draft_id)
    if current.revision != request.expected_revision:
        raise ConflictError("草稿已有新的保存版本，请重新读取后再修改。")
    business = current.business
    if request.business is not None:
        business = ScenarioBusiness.model_validate(
            {
                **business.model_dump(),
                **request.business.model_dump(exclude_unset=True),
            }
        )
    bindings = request.bindings or current.bindings
    settings = current.agent_settings
    if request.agent_settings is not None:
        settings = settings.model_copy(
            update=request.agent_settings.model_dump(exclude_unset=True)
        )
    changed = db.scalar(
        update(ScenarioDraft)
        .where(
            *_scope(project_id, staff_id),
            ScenarioDraft.id == draft_id,
            ScenarioDraft.revision == request.expected_revision,
        )
        .values(
            business=business.model_dump(mode="json"),
            bindings=bindings.model_dump(mode="json"),
            agent_settings=settings.model_dump(mode="json"),
            step=request.step or current.step,
            channel=request.channel or current.channel,
            revision=ScenarioDraft.revision + 1,
            updated_at=func.now(),
        )
        .returning(ScenarioDraft.id)
        .execution_options(synchronize_session=False)
    )
    if changed is None:
        db.rollback()
        raise ConflictError("草稿已有新的保存版本，请重新读取后再修改。")
    db.commit()
    return get_draft(db, project_id, staff_id, draft_id)

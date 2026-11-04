"""Current-authority subscriptions with private source binding."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.channel_memory_clearance import ChannelMemoryClearance
from app.models.platform import Platform
from app.models.project import Project
from app.models.session_command_delivery import SessionCommandDelivery
from app.models.staff import Staff
from app.models.visitor import Visitor
from app.models.visitor_session import VisitorSession
from app.models.workflow_event_subscription import (
    WorkflowEventDelivery,
    WorkflowEventSubscription,
)
from app.schemas.workflow_event_subscriptions import (
    WorkflowEventDeliveryList,
    WorkflowEventDeliverySummary,
    WorkflowEventSourceCheck,
    WorkflowEventSubscriptionSummary,
    WorkflowEventSubscriptionUpdate,
)
from app.services.workflow_client import workflow_client
from app.services.workflow_event_crypto import ensure_event_payload_crypto
from app.services.event_keyring import EventKeyringError
from app.services.company_entitlements import (
    new_service_available,
    require_new_service,
)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _admin_is_current(
    db: Session, project_id: UUID, staff_id: UUID | None
) -> bool:
    return (
        db.scalar(
            select(Staff.id)
            .join(Project)
            .where(
                Staff.id == staff_id,
                Staff.project_id == project_id,
                Staff.role == "admin",
                Staff.account_enabled.is_(True),
                Staff.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            )
        )
        is not None
    )


def current_subscription_authorized(
    db: Session, row: WorkflowEventSubscription
) -> bool:
    return bool(
        row.enabled
        and _admin_is_current(db, row.project_id, row.enabled_by_staff_id)
        and new_service_available(db, row.project_id)
    )


def _summary(
    workflow_id: str, row: WorkflowEventSubscription | None
) -> WorkflowEventSubscriptionSummary:
    if row is None:
        return WorkflowEventSubscriptionSummary(workflow_id=workflow_id)
    return WorkflowEventSubscriptionSummary(
        workflow_id=workflow_id,
        subscription_id=row.id,
        enabled=row.enabled,
        revision=row.revision,
        release_id=row.release_id,
        release_digest=row.release_digest,
        version=row.version,
        enabled_at=row.enabled_at,
    )


def get_subscription(
    db: Session, project_id: UUID, workflow_id: str
) -> WorkflowEventSubscriptionSummary:
    row = db.scalar(
        select(WorkflowEventSubscription).where(
            WorkflowEventSubscription.project_id == project_id,
            WorkflowEventSubscription.workflow_id == workflow_id,
        )
    )
    return _summary(workflow_id, row)


async def update_subscription(
    db: Session,
    actor: Staff,
    workflow_id: str,
    request: WorkflowEventSubscriptionUpdate,
) -> WorkflowEventSubscriptionSummary:
    project_id, actor_id = actor.project_id, actor.id
    if not _admin_is_current(db, project_id, actor_id):
        raise HTTPException(403, "当前管理员权限已失效")
    if request.enabled:
        require_new_service(db, project_id)
        try:
            ensure_event_payload_crypto()
        except EventKeyringError:
            raise HTTPException(503, "事件输入加密配置不可用，请核查服务端配置。") from None
    # No DB lock spans the upstream read. Recheck authority after the await.
    db.commit()
    publication = None
    if request.enabled:
        publication = await workflow_client.get_event_test_context(
            workflow_id, str(project_id)
        )
        if (
            publication.workflow_id != workflow_id
            or publication.event_type != "staff.session.closed"
            or publication.release_id != request.expected_release_id
            or publication.release_digest != request.expected_release_digest
        ):
            raise HTTPException(409, "已发布版本或事件来源已变化，请重新读取后开启")
    if not _admin_is_current(db, project_id, actor_id):
        raise HTTPException(403, "当前管理员权限已失效")
    if request.enabled:
        require_new_service(db, project_id)
    row = db.scalar(
        select(WorkflowEventSubscription)
        .where(
            WorkflowEventSubscription.project_id == project_id,
            WorkflowEventSubscription.workflow_id == workflow_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    revision = row.revision if row else 0
    if revision != request.expected_revision:
        raise HTTPException(409, "自动触发设置已变化，请刷新后操作")
    if row is None:
        row = WorkflowEventSubscription(
            project_id=project_id, workflow_id=workflow_id
        )
        db.add(row)
    row.revision = revision + 1
    row.enabled = request.enabled
    if publication is not None:
        row.release_id = publication.release_id
        row.release_digest = publication.release_digest
        row.version = publication.version
        row.enabled_at = datetime.now(timezone.utc)
        row.enabled_by_staff_id = actor_id
    try:
        db.flush()
        # Disable/rebind revokes earlier intents and clears retained bodies.
        db.execute(
            update(WorkflowEventDelivery)
            .where(
                WorkflowEventDelivery.subscription_id == row.id,
                WorkflowEventDelivery.subscription_revision != row.revision,
                WorkflowEventDelivery.status.in_(["pending", "sending"]),
            )
            .values(
                status="cancelled",
                encrypted_payload=None,
                lease_until=None,
                last_error="subscription_changed",
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "自动触发设置已变化，请刷新后操作") from None
    db.refresh(row)
    return _summary(workflow_id, row)


def list_deliveries(
    db: Session, project_id: UUID, workflow_id: str
) -> WorkflowEventDeliveryList:
    rows = db.scalars(
        select(WorkflowEventDelivery)
        .where(
            WorkflowEventDelivery.project_id == project_id,
            WorkflowEventDelivery.workflow_id == workflow_id,
        )
        .order_by(WorkflowEventDelivery.created_at.desc())
        .limit(20)
    ).all()
    return WorkflowEventDeliveryList(
        items=[
            WorkflowEventDeliverySummary.model_validate(row) for row in rows
        ]
    )


def verify_source_binding(
    db: Session, query: WorkflowEventSourceCheck
) -> WorkflowEventSourceCheck:
    """Verify current authority and the committed, same-tenant close fact."""
    binding = query.source_binding
    sub = db.scalar(
        select(WorkflowEventSubscription)
        .where(
            WorkflowEventSubscription.id == binding.subscription_id,
            WorkflowEventSubscription.project_id == query.project_id,
            WorkflowEventSubscription.workflow_id == query.workflow_id,
        )
        .execution_options(populate_existing=True)
    )
    delivery = db.scalar(
        select(WorkflowEventDelivery)
        .where(
            WorkflowEventDelivery.id == binding.delivery_id,
            WorkflowEventDelivery.project_id == query.project_id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        sub is None
        or delivery is None
        or not current_subscription_authorized(db, sub)
        or sub.revision != binding.revision
        or sub.enabled_at is None
        or sub.release_id != query.release_id
        or sub.release_digest != query.release_digest
        or sub.event_type != query.event_type
        or delivery.subscription_id != sub.id
        or delivery.subscription_revision != binding.revision
        or delivery.workflow_id != query.workflow_id
        or delivery.source_command_id != binding.source_command_id
        or delivery.session_id != binding.session_id
        or delivery.release_id != query.release_id
        or delivery.release_digest != query.release_digest
        or delivery.status not in {"pending", "sending", "accepted"}
    ):
        raise HTTPException(409, "事件订阅或来源授权已失效")
    source = db.scalar(
        select(SessionCommandDelivery)
        .where(
            SessionCommandDelivery.id == binding.source_command_id,
            SessionCommandDelivery.project_id == query.project_id,
            SessionCommandDelivery.session_id == binding.session_id,
            SessionCommandDelivery.kind == "close",
        )
        .execution_options(populate_existing=True)
    )
    session = db.scalar(
        select(VisitorSession)
        .where(
            VisitorSession.id == binding.session_id,
            VisitorSession.project_id == query.project_id,
            VisitorSession.status == "closed",
        )
        .execution_options(populate_existing=True)
    )
    if (
        source is None
        or session is None
        or session.closed_at is None
        or source.visitor_id != session.visitor_id
        or _utc(source.created_at) < _utc(sub.enabled_at)
        or _utc(session.updated_at) != _utc(source.session_version)
    ):
        raise HTTPException(409, "已结束会话的来源记录已失效")
    visitor = db.scalar(
        select(Visitor.id)
        .join(Platform, Platform.id == Visitor.platform_id)
        .where(
            Visitor.id == session.visitor_id,
            Visitor.project_id == query.project_id,
            Visitor.deleted_at.is_(None),
            Visitor.access_retired_at.is_(None),
            Platform.project_id == query.project_id,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
    )
    if visitor is None:
        raise HTTPException(409, "当前会话访问权限已失效")
    # A clear after this intent was staged invalidates its frozen excerpt.
    cleared = db.scalar(
        select(ChannelMemoryClearance.id)
        .where(
            ChannelMemoryClearance.project_id == query.project_id,
            ChannelMemoryClearance.channel_id == source.channel_id,
            ChannelMemoryClearance.channel_type == source.channel_type,
            ChannelMemoryClearance.user_id.in_(
                [
                    sub.enabled_by_staff_id,
                    source.actor_staff_id,
                    session.visitor_id,
                ]
            ),
            ChannelMemoryClearance.updated_at
            >= _utc(delivery.created_at).replace(tzinfo=None),
        )
        .limit(1)
    )
    if cleared is not None:
        raise HTTPException(409, "会话记录已清理，停止处理旧片段")
    return query

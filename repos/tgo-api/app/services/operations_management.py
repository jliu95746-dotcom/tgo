"""Tenant-scoped operations tools. All writes are serialized and audited."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models import Project, Staff, VisitorSession
from app.models.billing import BillingAudit, BillingJob, BillingOrder, InvoiceRequest
from app.models.company_account import CompanyAccount
from app.models.platform_operator import PlatformOperator
from app.schemas.operations_management import (
    CompanyDetail,
    CompanyDirectory,
    ManagedMember,
    MemberControl,
    OperationsOverview,
    OperatorAuditView,
)
from app.services.billing_quotes import conflict
from app.services.company_email import enqueue_action
from app.services.company_membership import (
    ensure_seat,
    lock_company,
    protect_last_admin,
)
from app.services.operations_companies import companies


def effective_status() -> ColumnElement[str]:
    now = datetime.now(timezone.utc)
    return case(
        (
            CompanyAccount.status.in_(["active", "trial"])
            & (CompanyAccount.expires_at <= now),
            "expired",
        ),
        else_=func.coalesce(CompanyAccount.status, "legacy"),
    )


def directory(
    db: Session,
    offset: int,
    limit: int,
    *,
    q: str,
    state: str | None,
    expiring: bool,
) -> CompanyDirectory:
    query = select(Project.id).outerjoin(
        CompanyAccount, Project.id == CompanyAccount.project_id
    )
    query = query.where(Project.deleted_at.is_(None))
    if q.strip():
        pattern = f"%{q.strip()}%"
        member_match = (
            select(Staff.id)
            .where(
                Staff.project_id == Project.id,
                Staff.role != "agent",
                Staff.deleted_at.is_(None),
                Staff.username.ilike(pattern),
            )
            .exists()
        )
        query = query.where(or_(Project.name.ilike(pattern), member_match))
    if state == "enabled":
        query = query.where(effective_status().in_(["active", "trial"]))
    elif state:
        query = query.where(effective_status() == state)
    if expiring:
        query = query.where(
            effective_status().in_(["active", "trial"]),
            CompanyAccount.expires_at <= datetime.now(timezone.utc) + timedelta(days=7),
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    ids = db.scalars(
        query.order_by(Project.created_at.desc(), Project.id)
        .offset(offset)
        .limit(limit)
    )
    rows = [
        row for identifier in ids for row in companies(db, 0, 1, project_id=identifier)
    ]
    return CompanyDirectory(data=rows, total=total, offset=offset, limit=limit)


def open_sessions(db: Session, target: Staff) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(VisitorSession)
            .where(
                VisitorSession.project_id == target.project_id,
                VisitorSession.staff_id == target.id,
                VisitorSession.status == "open",
            )
        )
        or 0
    )


def member_view(db: Session, target: Staff) -> ManagedMember:
    return ManagedMember(
        id=target.id,
        username=target.username,
        name=target.name or target.nickname,
        role=target.role,
        account_enabled=target.account_enabled,
        email_verified=target.email_verified_at is not None,
        token_version=target.token_version,
        open_sessions=open_sessions(db, target),
    )


def audit_log(
    db: Session, offset: int, limit: int, project_id: UUID | None = None
) -> list[OperatorAuditView]:
    query = select(BillingAudit, PlatformOperator.name).outerjoin(
        PlatformOperator,
        BillingAudit.operator_id == PlatformOperator.id,
    )
    if project_id is not None:
        query = query.where(BillingAudit.project_id == project_id)
    rows = db.execute(
        query.order_by(BillingAudit.created_at.desc(), BillingAudit.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        OperatorAuditView(
            id=row.id,
            project_id=row.project_id,
            operator_name=name or "系统",
            action=row.action,
            reason=row.reason,
            created_at=row.created_at,
            detail=row.detail,
        )
        for row, name in rows
    ]


def company_detail(db: Session, project_id: UUID) -> CompanyDetail:
    project = db.scalar(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if project is None:
        raise HTTPException(404, "企业不存在")
    account = db.get(CompanyAccount, project_id)
    members = db.scalars(
        select(Staff)
        .where(
            Staff.project_id == project_id,
            Staff.role != "agent",
            Staff.deleted_at.is_(None),
        )
        .order_by(Staff.created_at, Staff.id)
    )
    return CompanyDetail(
        company=companies(db, 0, 1, project_id=project_id)[0],
        created_at=project.created_at,
        plan_id=account.plan_id if account else None,
        version=account.version if account else 0,
        operator_override_until=account.operator_override_until if account else None,
        members=[member_view(db, target) for target in members],
        audits=audit_log(db, 0, 20, project_id),
    )


def locked_member(
    db: Session, project_id: UUID, member_id: UUID
) -> tuple[CompanyAccount | None, Staff]:
    account = lock_company(db, project_id)
    target = db.scalar(
        select(Staff)
        .where(
            Staff.id == member_id,
            Staff.project_id == project_id,
            Staff.role != "agent",
            Staff.deleted_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if target is None:
        raise HTTPException(404, "商家账号不存在")
    return account, target


def update_member(
    db: Session,
    operator: PlatformOperator,
    project_id: UUID,
    member_id: UUID,
    payload: MemberControl,
) -> ManagedMember:
    account, target = locked_member(db, project_id, member_id)
    if target.token_version != payload.expected_token_version:
        raise conflict("账号已被修改，请刷新后重新操作", "MEMBER_CONFLICT")
    role = payload.role if payload.role is not None else target.role
    enabled = (
        payload.account_enabled
        if payload.account_enabled is not None
        else target.account_enabled
    )
    if role == target.role and enabled == target.account_enabled:
        return member_view(db, target)
    if not enabled or role != "admin":
        protect_last_admin(db, target)
    if not enabled and open_sessions(db, target):
        raise conflict("账号仍在接待顾客，请先在客服后台转交会话")
    if enabled and not target.account_enabled:
        if account is None or target.email_verified_at is None:
            raise conflict("启用账号需要有效商家授权和已验证邮箱")
        ensure_seat(db, account)
    before = member_view(db, target)
    target.role, target.account_enabled = role, enabled
    target.token_version += 1
    if not enabled:
        target.status = "offline"
    after = member_view(db, target)
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=project_id,
            action="member.change",
            reason=payload.reason,
            detail={
                "before": before.model_dump(mode="json"),
                "after": after.model_dump(mode="json"),
            },
        )
    )
    db.flush()
    return after


def send_reset_email(
    db: Session,
    operator: PlatformOperator,
    project_id: UUID,
    member_id: UUID,
    reason: str,
) -> None:
    _, target = locked_member(db, project_id, member_id)
    if not target.account_enabled or target.email_verified_at is None:
        raise conflict("仅可向已验证且启用的账号发送密码重置邮件")
    enqueue_action(db, target, "reset")
    db.add(
        BillingAudit(
            operator_id=operator.id,
            project_id=project_id,
            action="member.reset_email",
            reason=reason,
            detail={"member_id": str(target.id)},
        )
    )


def overview(db: Session) -> OperationsOverview:
    now = datetime.now(timezone.utc)
    merchant = select(Project.id).where(Project.deleted_at.is_(None))
    active = merchant.join(
        CompanyAccount, CompanyAccount.project_id == Project.id
    ).where(
        effective_status().in_(["active", "trial"]),
    )

    def count(query: Select[tuple[UUID]]) -> int:
        return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    return OperationsOverview(
        total_companies=count(merchant),
        enabled_companies=count(active),
        expiring_companies=count(
            active.where(CompanyAccount.expires_at <= now + timedelta(days=7))
        ),
        unresolved_orders=count(
            select(BillingOrder.id).where(
                BillingOrder.project_id.in_(merchant),
                BillingOrder.payment_status == "paid",
                BillingOrder.fulfillment_status != "applied",
                BillingOrder.refunded_amount < BillingOrder.amount,
            )
        ),
        failed_tasks=count(
            select(BillingJob.id).where(
                or_(
                    BillingJob.status.in_(["failed", "manual_review"]),
                    (BillingJob.status == "pending")
                    & BillingJob.last_error.is_not(None),
                )
            )
        ),
        pending_invoices=count(
            select(InvoiceRequest.id).where(InvoiceRequest.status == "requested")
        ),
        checked_at=now,
    )

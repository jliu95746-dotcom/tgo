"Existing customer bindings cannot silently repurpose a visitor's \
private history."

import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError
from app.models.business_customer_identity import BusinessCustomerBinding
from app.models.visitor_business_scope import VisitorBusinessScope
from app.services.business_customer_identity_crypto import (
    CustomerBindingVault,
    session_signing_key,
)

MAX_VISITOR_BUSINESS_SCOPES = 20


def fingerprint(project: UUID, connection: UUID, customer_id: str) -> str:
    return hmac.new(
        session_signing_key(),
        b"visitor-business-owner:v1:"
        + str(project).encode()
        + b":"
        + str(connection).encode()
        + b":"
        + customer_id.encode(),
        hashlib.sha256,
    ).hexdigest()


def scopes(
    db: Session, project: UUID, visitor: UUID
) -> list[VisitorBusinessScope]:
    return list(
        db.scalars(
            select(VisitorBusinessScope)
            .where(
                VisitorBusinessScope.project_id == project,
                VisitorBusinessScope.visitor_id == visitor,
            )
            .execution_options(populate_existing=True)
            .limit(MAX_VISITOR_BUSINESS_SCOPES + 1)
        ).all()
    )


def ensure_owner(
    db: Session,
    project: UUID,
    visitor: UUID,
    connection: UUID,
    customer_id: str,
) -> VisitorBusinessScope:
    # The caller locks the connection and the current visitor before creating a
    # binding.
    expected = fingerprint(project, connection, customer_id)
    row: VisitorBusinessScope | None = db.scalar(
        select(VisitorBusinessScope)
        .where(
            VisitorBusinessScope.project_id == project,
            VisitorBusinessScope.visitor_id == visitor,
            VisitorBusinessScope.connection_id == connection,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        if len(scopes(db, project, visitor)) >= MAX_VISITOR_BUSINESS_SCOPES:
            raise ConflictError("当前访客已达到客户身份连接上限，请建立新的访客会话")
        history = list(
            db.scalars(
                select(BusinessCustomerBinding)
                .where(
                    BusinessCustomerBinding.project_id == project,
                    BusinessCustomerBinding.visitor_id == visitor,
                    BusinessCustomerBinding.connection_id == connection,
                )
                .limit(201)
            ).all()
        )
        if len(history) > 200:
            raise ConflictError("旧访客业务身份需要核对，请建立新的访客会话")
        for binding in history:
            original = (
                CustomerBindingVault()
                .decrypt(binding.customer_ciphertext, project, binding.id)
                .token
            )
            if original is None or not hmac.compare_digest(
                fingerprint(project, connection, original.get_secret_value()),
                expected,
            ):
                raise ConflictError(
                    "不能将其他客户的旧访客会话重新绑定，请建立新的访客会话",
                    details={"reason": "visitor_owner_changed"},
                )
        row = VisitorBusinessScope(
            project_id=project,
            visitor_id=visitor,
            connection_id=connection,
            owner_fingerprint=expected,
        )
        db.add(row)
        db.flush()
    if row.retired_at is not None:
        raise ConflictError(
            "原客户会话已结束，请建立新的访客会话", details={"reason": "visitor_access_ended"}
        )
    if not hmac.compare_digest(row.owner_fingerprint, expected):
        raise ConflictError(
            "客户已改变，请建立新的访客会话", details={"reason": "visitor_owner_changed"}
        )
    return row


def require_owner(
    db: Session,
    project: UUID,
    visitor: UUID,
    connection: UUID,
    customer_id: str,
) -> VisitorBusinessScope:
    row: VisitorBusinessScope | None = db.scalar(
        select(VisitorBusinessScope)
        .where(
            VisitorBusinessScope.project_id == project,
            VisitorBusinessScope.visitor_id == visitor,
            VisitorBusinessScope.connection_id == connection,
        )
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.retired_at is not None
        or not hmac.compare_digest(
            row.owner_fingerprint,
            fingerprint(project, connection, customer_id),
        )
    ):
        raise ConflictError("客户会话归属尚未核验或已结束，请重新建立访客会话")
    return row

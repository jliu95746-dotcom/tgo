"""Possession checks preserve anonymous chat without exposing private histories by ID."""

from datetime import UTC, datetime
import hmac
import secrets

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.exceptions import TGOAPIException
from app.models.visitor import Visitor
from app.models.platform import Platform
from app.models.project import Project
from app.models.business_customer_identity import BusinessCustomerBinding
from app.models.visitor_business_scope import VisitorBusinessScope
from app.models.visitor_session import VisitorSession
from app.models.ai_interaction_run import AIInteractionRun
from app.models.ai_execution_attempt import AIExecutionAttempt
from app.models.visitor_waiting_queue import VisitorWaitingQueue
from app.schemas.visitor_access import (
    VisitorAccessInput,
    VisitorBusinessAccess,
    VisitorIMBusinessReference,
)
from app.services.business_customer_identity import BusinessCustomerIdentityService
from app.services.business_customer_identity_crypto import issue_session
from app.services.visitor_business_scope import scopes
from app.services.visitor_access_crypto import access_digest


def denied() -> TGOAPIException:
    return TGOAPIException(
        "当前访客访问凭据或客户身份已失效，请重新建立会话",
        code="VISITOR_ACCESS_REQUIRED",
        status_code=401,
    )


def current_visitor(
    db: Session, visitor: Visitor, *, allow_retired: bool = False
) -> Visitor:
    query = (
        select(Visitor)
        .join(Platform, Platform.id == Visitor.platform_id)
        .join(Project, Project.id == Visitor.project_id)
    )
    if not allow_retired:
        query = query.where(
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
            Project.deleted_at.is_(None),
        )
    current = db.scalar(
        query.where(
            Visitor.id == visitor.id,
            Visitor.project_id == visitor.project_id,
            Visitor.platform_id == visitor.platform_id,
            Visitor.deleted_at.is_(None),
        )
        .with_for_update(of=Visitor)
        .execution_options(populate_existing=True)
    )
    if current is None or (current.access_retired_at is not None and not allow_retired):
        raise denied()
    return current


def protected(db: Session, visitor: Visitor) -> bool:
    return bool(
        scopes(db, visitor.project_id, visitor.id)
        or db.scalar(
            select(BusinessCustomerBinding.id)
            .where(
                BusinessCustomerBinding.project_id == visitor.project_id,
                BusinessCustomerBinding.visitor_id == visitor.id,
            )
            .limit(1)
        )
    )


def business_access(
    db: Session, visitor: Visitor, proof: VisitorAccessInput
) -> VisitorBusinessAccess:
    rows = scopes(db, visitor.project_id, visitor.id)
    if not rows:
        if protected(db, visitor):
            raise denied()
        if proof.customer_sessions:
            raise denied()
        return VisitorBusinessAccess(references=[], expires_at=None)
    if visitor.access_retired_at is not None or any(
        row.retired_at is not None for row in rows
    ):
        raise denied()
    connections = {row.connection_id for row in rows}
    supplied = [row.connection_id for row in proof.customer_sessions]
    if len(supplied) != len(set(supplied)) or set(supplied) != connections:
        raise denied()
    identity = BusinessCustomerIdentityService(db)
    verified = [
        identity.resolve(
            visitor.project_id,
            session.connection_id,
            visitor.id,
            session.customer_session,
        )
        for session in proof.customer_sessions
    ]
    references = [
        VisitorIMBusinessReference(
            scope_id=row.id,
            scope_revision=row.revision,
            connection_id=row.connection_id,
            binding_id=next(
                item.binding_id
                for item in verified
                if item.connection_id == row.connection_id
            ),
            binding_revision=next(
                item.revision
                for item in verified
                if item.connection_id == row.connection_id
            ),
        )
        for row in rows
    ]
    return VisitorBusinessAccess(
        references=references, expires_at=min(item.expires_at for item in verified)
    )


def check_im_business_references(
    db: Session, visitor: Visitor, refs: list[VisitorIMBusinessReference]
) -> None:
    rows = scopes(db, visitor.project_id, visitor.id)
    if len(rows) != len(refs) or len({item.connection_id for item in refs}) != len(
        refs
    ):
        raise denied()
    if not rows and protected(db, visitor):
        raise denied()
    identity = BusinessCustomerIdentityService(db)
    for scope in rows:
        ref = next(
            (item for item in refs if item.connection_id == scope.connection_id), None
        )
        if (
            ref is None
            or scope.retired_at is not None
            or ref.scope_id != scope.id
            or ref.scope_revision != scope.revision
        ):
            raise denied()
        binding = db.get(
            BusinessCustomerBinding, ref.binding_id, populate_existing=True
        )
        if (
            binding is None
            or binding.visitor_id != visitor.id
            or binding.project_id != visitor.project_id
            or binding.connection_id != scope.connection_id
        ):
            raise denied()
        current = identity.resolve(
            visitor.project_id,
            scope.connection_id,
            visitor.id,
            issue_session(binding).session_token,
        )
        if (
            current.revision != ref.binding_revision
            or current.expires_at <= datetime.now(UTC)
        ):
            raise denied()


def require_access(
    db: Session,
    visitor: Visitor,
    proof: VisitorAccessInput,
    *,
    allow_customer: bool = True,
) -> VisitorBusinessAccess:
    visitor = current_visitor(db, visitor)
    if proof.im_token is not None and not proof.customer_sessions:
        from app.services.im_access import authenticate_im_actor

        actor = authenticate_im_actor(
            db, f"{visitor.id}-vtr", proof.im_token.get_secret_value()
        )
        if (
            not isinstance(actor, Visitor)
            or actor.id != visitor.id
            or actor.platform_id != visitor.platform_id
        ):
            raise denied()
        from app.services.im_access import read_visitor_im_access

        claims = read_visitor_im_access(proof.im_token.get_secret_value())
        if claims.access_revision != visitor.access_revision and (
            claims.access_revision is not None
            or visitor.access_secret_digest is not None
        ):
            raise denied()
        check_im_business_references(db, visitor, claims.business_references)
        # Original signed expiry is preserved; no token can renew a customer session.
        return VisitorBusinessAccess(
            references=claims.business_references,
            expires_at=datetime.fromtimestamp(claims.exp, UTC)
            if claims.business_references
            else None,
        )
    business = business_access(db, visitor, proof)
    secret = proof.visitor_access_token
    owned = (
        secret is not None
        and visitor.access_secret_digest is not None
        and hmac.compare_digest(
            access_digest(visitor, secret), visitor.access_secret_digest
        )
    )
    if not owned and not (allow_customer and business.references):
        raise denied()
    return business


def registration_access(
    db: Session, visitor: Visitor, proof: VisitorAccessInput, *, new: bool
) -> tuple[SecretStr, VisitorBusinessAccess]:
    visitor = current_visitor(db, visitor)
    if new and visitor.access_secret_digest is not None:
        raise denied()
    # A new merchant account cannot resume cached customer history by its old IM JWT.
    business = (
        business_access(db, visitor, proof)
        if new or protected(db, visitor)
        else require_access(db, visitor, proof)
    )
    if visitor.access_secret_digest is not None:
        if proof.visitor_access_token is not None and hmac.compare_digest(
            access_digest(visitor, proof.visitor_access_token),
            visitor.access_secret_digest,
        ):
            return proof.visitor_access_token, business
        # A signed customer/IM proof can rotate browser access but not extend identity expiry.
        visitor.access_revision += 1
    secret = SecretStr(secrets.token_urlsafe(32))
    visitor.access_secret_digest = access_digest(visitor, secret)
    db.flush()
    return secret, business


def end_access(db: Session, visitor: Visitor, proof: VisitorAccessInput, *, end_token: SecretStr | None = None) -> None:
    # Possession of browser access is sufficient to end its own session; never reads data.
    visitor = current_visitor(db, visitor, allow_retired=True)
    secret = proof.visitor_access_token
    owns_access = (
        secret is not None
        and visitor.access_secret_digest is not None
        and hmac.compare_digest(
            access_digest(visitor, secret), visitor.access_secret_digest
        )
    )
    from app.services.visitor_end_grant import verifies_original_end

    if not owns_access and not verifies_original_end(visitor, end_token):
        raise denied()
    if visitor.access_retired_at is not None:
        db.commit()  # Release the receipt lock before retrying external cleanup.
        return  # A lost response can repeat ending; it can never resume or read data.
    now = datetime.now(UTC)
    visitor.access_retired_at = now
    visitor.is_online = False
    # Retain only its one-way digest for an idempotent end receipt. All read/resume checks reject retirement.
    visitor.access_revision += 1
    db.execute(
        update(VisitorBusinessScope)
        .where(
            VisitorBusinessScope.project_id == visitor.project_id,
            VisitorBusinessScope.visitor_id == visitor.id,
            VisitorBusinessScope.retired_at.is_(None),
        )
        .values(retired_at=now, revision=VisitorBusinessScope.revision + 1)
    )
    db.execute(
        update(BusinessCustomerBinding)
        .where(
            BusinessCustomerBinding.project_id == visitor.project_id,
            BusinessCustomerBinding.visitor_id == visitor.id,
            BusinessCustomerBinding.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    db.execute(
        update(VisitorSession)
        .where(
            VisitorSession.project_id == visitor.project_id,
            VisitorSession.visitor_id == visitor.id,
            VisitorSession.status == "open",
        )
        .values(status="closed", closed_at=now)
    )
    running_interactions = select(AIInteractionRun.id).where(
        AIInteractionRun.project_id == visitor.project_id,
        AIInteractionRun.visitor_id == visitor.id,
        AIInteractionRun.status == "running",
    )
    db.execute(
        update(AIExecutionAttempt)
        .where(
            AIExecutionAttempt.project_id == visitor.project_id,
            AIExecutionAttempt.interaction_run_id.in_(running_interactions),
            AIExecutionAttempt.status == "running",
        )
        .values(status="failed", completed_at=now)
    )
    db.execute(
        update(AIInteractionRun)
        .where(
            AIInteractionRun.project_id == visitor.project_id,
            AIInteractionRun.visitor_id == visitor.id,
            AIInteractionRun.status == "running",
        )
        .values(status="failed", error_message="visitor_access_ended", completed_at=now, updated_at=now)
    )
    db.execute(
        update(VisitorWaitingQueue)
        .where(
            VisitorWaitingQueue.project_id == visitor.project_id,
            VisitorWaitingQueue.visitor_id == visitor.id,
            VisitorWaitingQueue.status == "waiting",
        )
        .values(status="cancelled", exited_at=now, updated_at=now)
    )
    db.commit()

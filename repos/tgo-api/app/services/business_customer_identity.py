"""Authenticate merchant assertions and fence each individual customer session."""

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import hmac
import json
from typing import Literal, Protocol
from uuid import UUID, uuid4

from jose import JWTError, jwt
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError, TGOAPIException
from app.models.business_connection import BusinessConnection
from app.models.business_customer_identity import (
    BusinessCustomerBinding,
    BusinessCustomerIdentityPolicy,
)
from app.models.platform import Platform
from app.models.project import Project
from app.models.staff import Staff
from app.models.visitor import Visitor
from app.schemas.business_connections import BusinessConnectionCredentials
from app.schemas.business_customer_identity import (
    CustomerIdentityBindingView,
    CustomerIdentityPolicyView,
    CustomerIdentityPolicyWrite,
    CustomerIdentitySession,
    CustomerSessionClaims,
    VerifiedBusinessCustomer,
)
from app.services.business_customer_identity_crypto import (
    CustomerBindingVault,
    CustomerIssuerVault,
    identity_denied,
    parse_assertion,
    verify_signature,
    issue_session,
    session_signing_key,
    utc,
)
from app.services.visitor_business_scope import ensure_owner, require_owner
from app.services.visitor_access_crypto import access_digest
from app.models.visitor_business_scope import VisitorBusinessScope


class VisitorPlatformIdentity(Protocol):
    type: str


class VisitorIdentity(Protocol):
    id: UUID
    project_id: UUID
    platform_id: UUID
    deleted_at: datetime | None
    access_secret_digest: str | None

    @property
    def platform(self) -> VisitorPlatformIdentity:
        ...


VisitorIdentityReader = Callable[[UUID, UUID], VisitorIdentity | None]


class BusinessCustomerIdentityService:
    def __init__(
        self,
        db: Session,
        *,
        visitor_reader: VisitorIdentityReader | None = None,
        platform_reader: Callable[[UUID, UUID], bool] | None = None,
        actor_reader: Callable[[UUID, UUID], bool] | None = None,
    ) -> None:
        self.db = db
        self.visitor_reader = visitor_reader or self._visitor
        self.platform_reader = platform_reader or self._platform
        self.actor_reader = actor_reader or self._admin

    def _admin(self, project: UUID, actor: UUID) -> bool:
        return (
            self.db.scalar(
                select(Staff.id)
                .join(Project, Project.id == Staff.project_id)
                .where(
                    Staff.id == actor,
                    Staff.project_id == project,
                    Staff.role == "admin",
                    Staff.account_enabled.is_(True),
                    Staff.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                )
            )
            is not None
        )

    def _platform(self, project: UUID, platform: UUID) -> bool:
        return (
            self.db.scalar(
                select(Platform.id)
                .join(Project, Project.id == Platform.project_id)
                .where(
                    Platform.id == platform,
                    Platform.project_id == project,
                    Platform.is_active.is_(True),
                    Platform.deleted_at.is_(None),
                    Project.deleted_at.is_(None),
                )
            )
            is not None
        )

    def _visitor(self, project: UUID, visitor: UUID) -> Visitor | None:
        row = self.db.scalar(
            select(Visitor)
            .where(
                Visitor.id == visitor,
                Visitor.project_id == project,
                Visitor.deleted_at.is_(None),
                Visitor.access_retired_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (
            row
            if row and self.platform_reader(project, row.platform_id)
            else None
        )

    def _require_admin(self, project: UUID, actor: UUID) -> None:
        if not self.actor_reader(project, actor):
            raise TGOAPIException(
                "当前账号无权管理客户身份验证", code="FORBIDDEN", status_code=403
            )

    def _connection(
        self, project: UUID, connection: UUID, *, lock: bool = False
    ) -> BusinessConnection:
        query = select(BusinessConnection).where(
            BusinessConnection.project_id == project,
            BusinessConnection.id == connection,
        )
        if lock:
            query = query.with_for_update()
        row = self.db.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise NotFoundError("Business connection")
        if row.mode != "http":
            raise TGOAPIException(
                "示例连接不能验证真实商户客户",
                code="INVALID_IDENTITY_CONNECTION",
                status_code=409,
            )
        return row

    def _policy(
        self, project: UUID, connection: UUID, *, lock: bool = False
    ) -> BusinessCustomerIdentityPolicy | None:
        query = select(BusinessCustomerIdentityPolicy).where(
            BusinessCustomerIdentityPolicy.project_id == project,
            BusinessCustomerIdentityPolicy.connection_id == connection,
        )
        if lock:
            query = query.with_for_update()
        return self.db.scalar(query.execution_options(populate_existing=True))

    @staticmethod
    def _view(
        row: BusinessCustomerIdentityPolicy,
    ) -> CustomerIdentityPolicyView:
        return CustomerIdentityPolicyView(
            id=row.id,
            project_id=row.project_id,
            connection_id=row.connection_id,
            platform_ids=[UUID(value) for value in row.platform_ids],
            enabled=row.enabled,
            revision=row.revision,
            connection_configuration_revision=row.connection_configuration_revision,
            max_session_seconds=row.max_session_seconds,
            signing_key_configured=bool(row.signing_ciphertext),
        )

    def read_policy(
        self, project: UUID, connection: UUID
    ) -> CustomerIdentityPolicyView:
        self._connection(project, connection)
        policy = self._policy(project, connection)
        if policy is None:
            raise NotFoundError("Customer identity configuration")
        return self._view(policy)

    def configure(
        self,
        project: UUID,
        actor: UUID,
        connection: UUID,
        payload: CustomerIdentityPolicyWrite,
    ) -> CustomerIdentityPolicyView:
        self._require_admin(project, actor)
        current = self._connection(project, connection, lock=True)
        if not all(
            self.platform_reader(project, platform)
            for platform in payload.platform_ids
        ):
            raise identity_denied()
        policy = self._policy(project, connection, lock=True)
        if payload.expected_revision != (policy.revision if policy else 0):
            raise ConflictError("客户身份验证配置已改变，请刷新")
        if policy is None:
            if payload.signing_secret is None:
                raise TGOAPIException(
                    "请配置独立商户签名密钥",
                    code="IDENTITY_SIGNER_REQUIRED",
                    status_code=422,
                )
            policy = BusinessCustomerIdentityPolicy(
                id=uuid4(),
                project_id=project,
                connection_id=connection,
                revision=1,
            )
            self.db.add(policy)
        else:
            policy.revision += 1
        if payload.signing_secret is not None:
            policy.signing_ciphertext = CustomerIssuerVault().encrypt(
                BusinessConnectionCredentials(token=payload.signing_secret),
                project,
                policy.id,
            )
        policy.platform_ids = [str(value) for value in payload.platform_ids]
        policy.connection_configuration_revision = (
            current.configuration_revision
        )
        policy.max_session_seconds = payload.max_session_seconds
        policy.enabled = payload.enabled
        policy.updated_by = actor
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictError("客户身份验证配置发生并发修改，请刷新") from None
        return self._view(policy)

    def _valid_scope(
        self,
        connection: BusinessConnection,
        policy: BusinessCustomerIdentityPolicy,
        project: UUID,
        visitor_id: UUID,
        platform_id: UUID,
        revision: int,
    ) -> VisitorIdentity:
        visitor = self.visitor_reader(project, visitor_id)
        if (
            not policy.enabled
            or revision != policy.revision
            or policy.connection_configuration_revision
            != connection.configuration_revision
            or visitor is None
            or visitor.deleted_at is not None
            or visitor.project_id != project
            or visitor.id != visitor_id
            or visitor.platform_id != platform_id
            or str(platform_id) not in policy.platform_ids
            or not self.platform_reader(project, platform_id)
        ):
            raise identity_denied()
        return visitor

    def exchange(
        self,
        connection_id: UUID,
        assertion: SecretStr,
        *,
        visitor_access_token: SecretStr | None = None,
        trusted_channel: bool = False,
    ) -> CustomerIdentitySession:
        claims, payload, signature = parse_assertion(assertion)
        if claims.connection_id != connection_id:
            raise identity_denied()
        connection = self._connection(
            claims.project_id, connection_id, lock=True
        )
        policy = self._policy(claims.project_id, connection_id, lock=True)
        if policy is None:
            raise identity_denied()
        secret = (
            CustomerIssuerVault()
            .decrypt(policy.signing_ciphertext, claims.project_id, policy.id)
            .token
        )
        if secret is None:
            raise identity_denied()
        verify_signature(secret, payload, signature)
        visitor = self._valid_scope(
            connection,
            policy,
            claims.project_id,
            claims.visitor_id,
            claims.platform_id,
            claims.policy_revision,
        )
        now = int(datetime.now(UTC).timestamp())
        if not (
            claims.issued_at <= now + 30
            and now
            < claims.expires_at
            <= claims.issued_at + policy.max_session_seconds
        ):
            raise identity_denied()
        canonical = json.dumps(
            claims.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hmac.new(
            session_signing_key(),
            b"customer-identity-proof:" + canonical,
            hashlib.sha256,
        ).hexdigest()
        row = self.db.scalar(
            select(BusinessCustomerBinding)
            .where(
                BusinessCustomerBinding.policy_id == policy.id,
                BusinessCustomerBinding.nonce == claims.nonce,
            )
            .execution_options(populate_existing=True)
        )
        if row is not None:
            if (
                not hmac.compare_digest(row.proof_digest, digest)
                or row.revoked_at is not None
                or utc(row.expires_at) <= datetime.now(UTC)
            ):
                raise ConflictError("原身份验证请求已失效或内容冲突，请重新验证")
        elif claims.issued_at < now - 60:
            raise identity_denied()
        existing_owner = self.db.scalar(
            select(VisitorBusinessScope.id).where(
                VisitorBusinessScope.project_id == claims.project_id,
                VisitorBusinessScope.visitor_id == claims.visitor_id,
                VisitorBusinessScope.connection_id == connection_id,
            )
        )
        if existing_owner is None:
            if trusted_channel:
                if visitor.platform.type == "website":
                    raise identity_denied()
            elif (
                visitor.platform.type != "website"
                or visitor_access_token is None
                or visitor.access_secret_digest is None
                or not hmac.compare_digest(
                    access_digest(visitor, visitor_access_token),
                    visitor.access_secret_digest,
                )
            ):
                raise identity_denied()
        ensure_owner(
            self.db,
            claims.project_id,
            claims.visitor_id,
            connection_id,
            claims.customer_id,
        )
        if row is None:
            identifier = uuid4()
            row = BusinessCustomerBinding(
                id=identifier,
                project_id=claims.project_id,
                connection_id=connection_id,
                policy_id=policy.id,
                visitor_id=claims.visitor_id,
                platform_id=claims.platform_id,
                nonce=claims.nonce,
                proof_digest=digest,
                customer_ciphertext=CustomerBindingVault().encrypt(
                    BusinessConnectionCredentials(
                        token=SecretStr(claims.customer_id)
                    ),
                    claims.project_id,
                    identifier,
                ),
                policy_revision=policy.revision,
                connection_configuration_revision=connection.configuration_revision,
                revision=1,
                issued_at=datetime.fromtimestamp(claims.issued_at, UTC),
                expires_at=datetime.fromtimestamp(claims.expires_at, UTC),
            )
            self.db.add(row)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictError("原身份验证请求正在处理，请按原请求查询") from None
        return issue_session(row)

    def resolve(
        self,
        project: UUID,
        connection_id: UUID,
        visitor_id: UUID,
        token: SecretStr,
    ) -> VerifiedBusinessCustomer:
        try:
            decoded = jwt.decode(
                token.get_secret_value(),
                session_signing_key(),
                algorithms=["HS256"],
                audience="tgo-business-customer-session",
                options={
                    "require_exp": True,
                    "require_sub": True,
                    "require_aud": True,
                    "require_iat": True,
                },
            )
            claims = CustomerSessionClaims.model_validate(decoded)
        except (JWTError, ValidationError, ValueError, TypeError):
            raise identity_denied() from None
        if (
            claims.project_id != project
            or claims.connection_id != connection_id
            or claims.visitor_id != visitor_id
        ):
            raise identity_denied()
        row = self.db.scalar(
            select(BusinessCustomerBinding)
            .where(
                BusinessCustomerBinding.id == claims.sub,
                BusinessCustomerBinding.project_id == project,
                BusinessCustomerBinding.connection_id == connection_id,
                BusinessCustomerBinding.visitor_id == visitor_id,
            )
            .execution_options(populate_existing=True)
        )
        connection = self._connection(project, connection_id)
        policy = self._policy(project, connection_id)
        if (
            row is None
            or policy is None
            or row.policy_id != policy.id
            or row.revoked_at is not None
            or row.revision != claims.revision
            or row.policy_revision != claims.policy_revision
            or row.platform_id != claims.platform_id
            or row.connection_configuration_revision
            != connection.configuration_revision
            or int(utc(row.issued_at).timestamp()) != claims.iat
            or int(utc(row.expires_at).timestamp()) != claims.exp
            or utc(row.expires_at) <= datetime.now(UTC)
        ):
            raise identity_denied()
        self._valid_scope(
            connection,
            policy,
            project,
            visitor_id,
            row.platform_id,
            row.policy_revision,
        )
        customer = (
            CustomerBindingVault()
            .decrypt(row.customer_ciphertext, project, row.id)
            .token
        )
        if customer is None:
            raise identity_denied()
        require_owner(
            self.db,
            project,
            visitor_id,
            connection_id,
            customer.get_secret_value(),
        )
        return VerifiedBusinessCustomer(
            project_id=project,
            connection_id=connection_id,
            visitor_id=visitor_id,
            platform_id=row.platform_id,
            binding_id=row.id,
            revision=row.revision,
            policy_revision=row.policy_revision,
            connection_configuration_revision=row.connection_configuration_revision,
            expires_at=utc(row.expires_at),
            customer_id=customer,
        )

    def revoke(
        self,
        project: UUID,
        actor: UUID,
        connection: UUID,
        binding: UUID,
        expected_revision: int,
    ) -> CustomerIdentityBindingView:
        self._require_admin(project, actor)
        current = self._connection(project, connection, lock=True)
        row = self.db.scalar(
            select(BusinessCustomerBinding)
            .where(
                BusinessCustomerBinding.project_id == project,
                BusinessCustomerBinding.connection_id == connection,
                BusinessCustomerBinding.id == binding,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFoundError("Customer identity binding")
        if row.revision != expected_revision:
            raise ConflictError("客户身份状态已改变，请刷新")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            row.revision += 1
            self.db.commit()
        return self._binding_view(
            row, self._policy(project, connection), current
        )

    def _binding_view(
        self,
        row: BusinessCustomerBinding,
        policy: BusinessCustomerIdentityPolicy | None,
        connection: BusinessConnection,
    ) -> CustomerIdentityBindingView:
        status: Literal["current", "expired", "revoked", "stale"] = (
            "revoked"
            if row.revoked_at
            else "expired"
            if utc(row.expires_at) <= datetime.now(UTC)
            else "stale"
            if (
                policy is None
                or not policy.enabled
                or row.policy_revision != policy.revision
                or row.connection_configuration_revision
                != connection.configuration_revision
            )
            else "current"
        )
        if status == "current" and policy is not None:
            try:
                self._valid_scope(
                    connection,
                    policy,
                    row.project_id,
                    row.visitor_id,
                    row.platform_id,
                    row.policy_revision,
                )
            except TGOAPIException:
                status = "stale"
        return CustomerIdentityBindingView(
            id=row.id,
            visitor_id=row.visitor_id,
            platform_id=row.platform_id,
            revision=row.revision,
            expires_at=utc(row.expires_at),
            revoked_at=utc(row.revoked_at) if row.revoked_at else None,
            status=status,
        )

    def list_bindings(
        self, project: UUID, connection: UUID, limit: int = 50
    ) -> list[CustomerIdentityBindingView]:
        current = self._connection(project, connection)
        policy = self._policy(project, connection)
        rows = self.db.scalars(
            select(BusinessCustomerBinding)
            .where(
                BusinessCustomerBinding.project_id == project,
                BusinessCustomerBinding.connection_id == connection,
            )
            .order_by(
                BusinessCustomerBinding.created_at.desc(),
                BusinessCustomerBinding.id.desc(),
            )
            .limit(limit)
        )
        return [self._binding_view(row, policy, current) for row in rows]

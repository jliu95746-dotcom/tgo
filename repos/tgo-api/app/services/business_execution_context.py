"Reuse the accepted request, live visitor conversation and \
merchant-signed identity."

from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
import hmac
from uuid import UUID
from typing import cast

from jose import JWTError, jwt
from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ai_interaction_run import AIInteractionRun
from app.models.business_customer_identity import BusinessCustomerBinding
from app.models.platform import Platform
from app.models.visitor_session import VisitorSession
from app.models.visitor import Visitor
from app.schemas.business_execution_context import (
    BusinessContextBinding,
    BusinessContextCheck,
    BusinessContextClaims,
    BusinessCustomerSessionInput,
    BusinessRequestAuthorization,
    BusinessRequestPrincipal,
)
from app.schemas.knowledge import KnowledgeChannel
from app.schemas.business_customer_identity import VerifiedBusinessCustomer
from app.services.business_customer_identity import (
    BusinessCustomerIdentityService,
    VisitorIdentity,
)
from app.services.business_customer_identity_crypto import (
    identity_denied,
    issue_session,
    session_signing_key,
)
from app.services.knowledge_channel import resolve_platform_knowledge_channel
from app.utils.encoding import build_visitor_channel_id

PlatformChannelReader = Callable[[UUID, UUID], KnowledgeChannel | None]
ServiceModeReader = Callable[[UUID, UUID, VisitorIdentity], str]


def context_signing_key() -> bytes:
    return hmac.new(
        session_signing_key(), b"business-request-context-v1", hashlib.sha256
    ).digest()


class BusinessExecutionContextService:
    def __init__(
        self,
        db: Session,
        *,
        identity: BusinessCustomerIdentityService | None = None,
        platform_channel_reader: PlatformChannelReader | None = None,
        service_mode_reader: ServiceModeReader | None = None,
    ):
        self.db = db
        self.identity = identity or BusinessCustomerIdentityService(db)
        self.platform_channel = platform_channel_reader or self._channel
        self.service_mode = service_mode_reader or self._mode

    def _mode(
        self, project: UUID, platform: UUID, visitor: VisitorIdentity
    ) -> str:
        from app.services.chat_service import resolve_service_mode

        row = self.db.scalar(
            select(Platform)
            .where(
                Platform.id == platform,
                Platform.project_id == project,
                Platform.deleted_at.is_(None),
                Platform.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        return (
            resolve_service_mode(row, cast(Visitor, visitor))
            if row is not None
            else "manual"
        )

    def _channel(
        self, project: UUID, platform: UUID
    ) -> KnowledgeChannel | None:
        row = self.db.scalar(
            select(Platform)
            .where(
                Platform.id == platform,
                Platform.project_id == project,
                Platform.deleted_at.is_(None),
                Platform.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        return (
            resolve_platform_knowledge_channel(row.type)
            if row is not None
            else None
        )

    def _parent(
        self,
        request: UUID,
        project: UUID,
        visitor: UUID,
        session_id: UUID | None = None,
    ) -> tuple[AIInteractionRun, VisitorSession, KnowledgeChannel]:
        run = self.db.scalar(
            select(AIInteractionRun)
            .where(
                AIInteractionRun.id == request,
                AIInteractionRun.project_id == project,
                AIInteractionRun.visitor_id == visitor,
            )
            .execution_options(populate_existing=True)
        )
        current = self.identity.visitor_reader(project, visitor)
        if (
            run is None
            or run.status != "running"
            or run.completed_at is not None
            or run.channel_type != 251
            or run.channel_id != build_visitor_channel_id(visitor)
            or current is None
            or current.deleted_at is not None
            or current.id != visitor
            or current.project_id != project
            or current.platform_id != run.platform_id
            or self.service_mode(project, run.platform_id, current) != "auto"
            or not self.identity.platform_reader(project, run.platform_id)
        ):
            raise identity_denied()
        channel = self.platform_channel(project, run.platform_id)
        if channel is None or channel == KnowledgeChannel.INTERNAL:
            raise identity_denied()
        query = select(VisitorSession).where(
            VisitorSession.project_id == project,
            VisitorSession.visitor_id == visitor,
            VisitorSession.platform_id == run.platform_id,
            VisitorSession.status == "open",
            VisitorSession.closed_at.is_(None),
            VisitorSession.staff_id.is_not(None),
        )
        if session_id is not None:
            query = query.where(VisitorSession.id == session_id)
        sessions = list(
            self.db.scalars(
                query.limit(2).execution_options(populate_existing=True)
            )
        )
        if len(sessions) != 1:
            raise identity_denied()
        return run, sessions[0], channel

    @staticmethod
    def _binding(verified: VerifiedBusinessCustomer) -> BusinessContextBinding:
        return BusinessContextBinding(
            connection_id=verified.connection_id,
            binding_id=verified.binding_id,
            binding_revision=verified.revision,
            policy_revision=verified.policy_revision,
            connection_configuration_revision=verified.connection_configuration_revision,
            expires_at=verified.expires_at,
        )

    def issue(
        self,
        request: UUID,
        project: UUID,
        visitor: UUID,
        sessions: list[BusinessCustomerSessionInput],
    ) -> BusinessRequestAuthorization:
        if (
            not sessions
            or len(sessions) > 20
            or len({row.connection_id for row in sessions}) != len(sessions)
        ):
            raise identity_denied()
        run, conversation, channel = self._parent(request, project, visitor)
        bindings: list[BusinessContextBinding] = []
        for item in sessions:
            verified = self.identity.resolve(
                project, item.connection_id, visitor, item.customer_session
            )
            if verified.platform_id != run.platform_id:
                raise identity_denied()
            bindings.append(self._binding(verified))
        now = int(datetime.now(UTC).timestamp())
        expiry = min(
            now + 300, *(int(row.expires_at.timestamp()) for row in bindings)
        )
        claims = BusinessContextClaims(
            project_id=project,
            visitor_id=visitor,
            channel=channel,
            request_id=run.id,
            request_fingerprint=run.request_fingerprint,
            platform_id=run.platform_id,
            visitor_session_id=conversation.id,
            source_message_id=run.source_message_id,
            response_client_msg_no=run.response_client_msg_no,
            bindings=bindings,
            iat=now,
            exp=expiry,
        )
        token = jwt.encode(
            claims.model_dump(mode="json"),
            context_signing_key(),
            algorithm="HS256",
        )
        return BusinessRequestAuthorization(
            project_id=project,
            visitor_id=visitor,
            channel=channel,
            request_id=run.id,
            context_digest=hashlib.sha256(token.encode()).hexdigest(),
            expires_at=datetime.fromtimestamp(expiry, UTC),
            context_token=SecretStr(token),
        )

    def verify(self, check: BusinessContextCheck) -> BusinessRequestPrincipal:
        try:
            decoded = jwt.decode(
                check.context_token.get_secret_value(),
                context_signing_key(),
                algorithms=["HS256"],
                audience="tgo-business-execution-context",
                options={
                    "require_exp": True,
                    "require_iat": True,
                    "require_aud": True,
                },
            )
            claims = BusinessContextClaims.model_validate(decoded)
        except (JWTError, ValidationError, ValueError, TypeError):
            raise identity_denied() from None
        now = datetime.now(UTC)
        if (
            claims.model_dump(
                include=set(BusinessContextCheck.model_fields)
                - {"context_token"}
            )
            != check.model_dump()
            or claims.iat > now.timestamp() + 30
        ):
            raise identity_denied()
        run, conversation, channel = self._parent(
            check.request_id,
            check.project_id,
            check.visitor_id,
            claims.visitor_session_id,
        )
        if (
            claims.platform_id != run.platform_id
            or claims.channel != channel
            or claims.source_message_id != run.source_message_id
            or claims.response_client_msg_no != run.response_client_msg_no
            or claims.request_fingerprint != run.request_fingerprint
        ):
            raise identity_denied()
        for item in claims.bindings:
            row = self.db.scalar(
                select(BusinessCustomerBinding)
                .where(
                    BusinessCustomerBinding.id == item.binding_id,
                    BusinessCustomerBinding.project_id == check.project_id,
                    BusinessCustomerBinding.connection_id
                    == item.connection_id,
                    BusinessCustomerBinding.visitor_id == check.visitor_id,
                )
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise identity_denied()
            # Reissue only this row's original, unchanged expiry for the
            # private
            # owner check. This neither renews nor revives the customer
            # session.
            verified = self.identity.resolve(
                check.project_id,
                item.connection_id,
                check.visitor_id,
                issue_session(row).session_token,
            )
            if (
                verified.platform_id != claims.platform_id
                or self._binding(verified) != item
            ):
                raise identity_denied()
        return BusinessRequestPrincipal(
            **check.model_dump(),
            platform_id=claims.platform_id,
            visitor_session_id=conversation.id,
            source_message_id=claims.source_message_id,
            response_client_msg_no=claims.response_client_msg_no,
            bindings=claims.bindings,
            context_digest=hashlib.sha256(
                check.context_token.get_secret_value().encode()
            ).hexdigest(),
            checked_at=datetime.now(UTC),
            expires_at=datetime.fromtimestamp(claims.exp, UTC),
        )

"""Persistent, PII-minimized audit sink for business queries."""

from __future__ import annotations

from types import TracebackType
from typing import Callable, Protocol, cast

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.domain.business_tools.models import BusinessQueryAuditEvent
from app.models.business_query_audit import BusinessQueryAuditRecord

logger = get_logger("business_query.audit")


class AuditSession(Protocol):
    """Small session surface needed by the audit sink."""

    def add(self, instance: object) -> None:
        """Stage one audit record."""
        ...

    async def commit(self) -> None:
        """Commit the record."""
        ...


class AuditSessionContext(Protocol):
    """Async context manager yielding an audit session."""

    async def __aenter__(self) -> AuditSession:
        """Open a session."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Close a session."""
        ...


AuditSessionFactory = Callable[[], AuditSessionContext]


class DatabaseBusinessQueryAuditSink:
    """Persist fingerprints and routing metadata, never raw customer data."""

    def __init__(
        self,
        session_factory: AuditSessionFactory | None = None,
    ) -> None:
        self._session_factory = session_factory or cast(
            AuditSessionFactory,
            AsyncSessionLocal,
        )

    async def record(self, event: BusinessQueryAuditEvent) -> None:
        record = BusinessQueryAuditRecord(
            tenant_id=event.tenant_id,
            conversation_id=event.conversation_id,
            request_id=event.request_id,
            actor_id=event.actor_id,
            operation=event.operation,
            outcome=event.outcome,
            visitor_fingerprint=event.visitor_fingerprint,
            parameter_fingerprint=event.parameter_fingerprint,
            duration_ms=event.duration_ms,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
        logger.info(
            "read_only_business_query_persisted",
            extra={
                "tenant_id": str(event.tenant_id),
                "operation": event.operation,
                "outcome": event.outcome,
            },
        )

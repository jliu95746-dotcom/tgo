"""Fail-closed orchestration for read-only order and logistics queries."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from time import monotonic

from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
    BusinessQueryOperation,
    BusinessQueryOutcome,
    LogisticsQueryInput,
    LogisticsQueryResult,
    OrderOwnership,
    OrderQueryInput,
    OrderQueryResult,
)
from app.domain.business_tools.providers import (
    BusinessQueryAuditSink,
    ReadOnlyBusinessProvider,
)


class BusinessQueryError(RuntimeError):
    """Base error that is safe to surface without provider details."""


class BusinessQueryAccessDenied(BusinessQueryError):
    """The requested record could not be proven to belong to the visitor."""


class BusinessQueryTimeout(BusinessQueryError):
    """The provider did not complete within the configured deadline."""


class BusinessQueryProviderError(BusinessQueryError):
    """The provider failed without exposing upstream details."""


class BusinessQueryAuditError(BusinessQueryError):
    """The required audit event could not be safely persisted."""


class BusinessQueryService:
    """Execute only read operations and verify all returned ownership evidence."""

    def __init__(
        self,
        provider: ReadOnlyBusinessProvider,
        audit_sink: BusinessQueryAuditSink,
        *,
        timeout_seconds: float,
        audit_fingerprint_key: bytes,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if len(audit_fingerprint_key) < 32:
            raise ValueError("audit_fingerprint_key must be at least 32 bytes")
        self._provider = provider
        self._audit_sink = audit_sink
        self._timeout_seconds = timeout_seconds
        self._audit_fingerprint_key = bytes(audit_fingerprint_key)

    async def query_order(
        self,
        query: OrderQueryInput,
        context: BusinessQueryContext,
    ) -> OrderQueryResult:
        """Query an order and return it only after ownership and audit checks."""

        started_at = monotonic()
        try:
            owned_result = await asyncio.wait_for(
                self._provider.query_order(query, context),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            await self._audit(
                operation="order_query",
                outcome="timeout",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryTimeout("业务查询超时，请稍后重试或转人工") from exc
        except Exception as exc:
            await self._audit(
                operation="order_query",
                outcome="failed",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryProviderError("业务查询暂时不可用") from exc

        if not self._ownership_matches(
            ownership=owned_result.ownership,
            result_order_no=owned_result.result.order_no,
            requested_order_no=query.order_no,
            context=context,
        ):
            await self._audit(
                operation="order_query",
                outcome="denied",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryAccessDenied("无法确认订单归属，请转人工核验")
        await self._audit(
            operation="order_query",
            outcome="success",
            order_no=query.order_no,
            context=context,
            started_at=started_at,
        )
        return owned_result.result

    async def query_logistics(
        self,
        query: LogisticsQueryInput,
        context: BusinessQueryContext,
    ) -> LogisticsQueryResult:
        """Query logistics and return it only after ownership and audit checks."""

        started_at = monotonic()
        try:
            owned_result = await asyncio.wait_for(
                self._provider.query_logistics(query, context),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            await self._audit(
                operation="logistics_query",
                outcome="timeout",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryTimeout("业务查询超时，请稍后重试或转人工") from exc
        except Exception as exc:
            await self._audit(
                operation="logistics_query",
                outcome="failed",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryProviderError("业务查询暂时不可用") from exc

        if not self._ownership_matches(
            ownership=owned_result.ownership,
            result_order_no=owned_result.result.order_no,
            requested_order_no=query.order_no,
            context=context,
        ):
            await self._audit(
                operation="logistics_query",
                outcome="denied",
                order_no=query.order_no,
                context=context,
                started_at=started_at,
            )
            raise BusinessQueryAccessDenied("无法确认订单归属，请转人工核验")
        await self._audit(
            operation="logistics_query",
            outcome="success",
            order_no=query.order_no,
            context=context,
            started_at=started_at,
        )
        return owned_result.result

    def _ownership_matches(
        self,
        *,
        ownership: OrderOwnership,
        result_order_no: str,
        requested_order_no: str,
        context: BusinessQueryContext,
    ) -> bool:
        return not (
            ownership.tenant_id != context.tenant_id
            or ownership.visitor_id != context.visitor_id
            or ownership.order_no != requested_order_no
            or result_order_no != requested_order_no
        )

    async def _audit(
        self,
        *,
        operation: BusinessQueryOperation,
        outcome: BusinessQueryOutcome,
        order_no: str,
        context: BusinessQueryContext,
        started_at: float,
    ) -> None:
        try:
            await self._audit_sink.record(
                BusinessQueryAuditEvent(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    request_id=context.request_id,
                    actor_id=context.actor_id,
                    operation=operation,
                    outcome=outcome,
                    visitor_fingerprint=self._fingerprint(
                        context.tenant_id.hex,
                        context.visitor_id,
                    ),
                    parameter_fingerprint=self._fingerprint(
                        context.tenant_id.hex,
                        order_no,
                    ),
                    duration_ms=(monotonic() - started_at) * 1000,
                )
            )
        except Exception as exc:
            raise BusinessQueryAuditError("业务查询审计失败，请转人工处理") from exc

    def _fingerprint(self, scope: str, value: str) -> str:
        return hmac.new(
            self._audit_fingerprint_key,
            f"{scope}\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

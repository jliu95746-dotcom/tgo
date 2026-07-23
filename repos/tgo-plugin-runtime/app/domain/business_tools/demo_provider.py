"""Development-only read provider with deterministic handbag-shop data."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
    LogisticsQueryInput,
    LogisticsQueryResult,
    OrderOwnership,
    OrderQueryInput,
    OrderQueryResult,
    OwnedLogisticsQueryResult,
    OwnedOrderQueryResult,
)

logger = get_logger("business_query.audit")


class DemoHandbagBusinessProvider:
    """Return tenant-bound sample data without exposing any write operation."""

    async def query_order(
        self,
        query: OrderQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedOrderQueryResult:
        ownership = self._ownership(query.order_no, context)
        return OwnedOrderQueryResult(
            ownership=ownership,
            result=OrderQueryResult(
                order_no=query.order_no,
                status="已发货",
                amount_minor=89900,
                currency="CNY",
                created_at=datetime(2026, 7, 16, 10, 30, tzinfo=UTC),
            ),
        )

    async def query_logistics(
        self,
        query: LogisticsQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedLogisticsQueryResult:
        ownership = self._ownership(query.order_no, context)
        return OwnedLogisticsQueryResult(
            ownership=ownership,
            result=LogisticsQueryResult(
                order_no=query.order_no,
                status="运输中，预计明日送达",
                carrier="顺丰速运",
                tracking_no_masked="SF****5678",
                updated_at=datetime(2026, 7, 22, 9, 0, tzinfo=UTC),
            ),
        )

    @staticmethod
    def _ownership(
        order_no: str,
        context: BusinessQueryContext,
    ) -> OrderOwnership:
        return OrderOwnership(
            tenant_id=context.tenant_id,
            visitor_id=context.visitor_id,
            order_no=order_no,
        )


class LoggingBusinessQueryAuditSink:
    """Write a PII-minimized structured event to the service log."""

    def __init__(self) -> None:
        self.events: list[BusinessQueryAuditEvent] = []

    async def record(self, event: BusinessQueryAuditEvent) -> None:
        self.events.append(event)
        logger.info(
            "read_only_business_query",
            extra={"business_query_audit": event.model_dump(mode="json")},
        )

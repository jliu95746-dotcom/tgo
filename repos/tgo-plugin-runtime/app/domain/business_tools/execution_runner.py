"""Formal read-only customer operations recheck the original grant after querying."""

from datetime import UTC, datetime
import re
from typing import Protocol, cast

from app.config import settings
from app.domain.business_tools.audit_sink import DatabaseBusinessQueryAuditSink
from app.domain.business_tools.connection_runner import ConnectionTestRunner
from app.domain.business_tools.customer_tools import (
    CustomerHTTPConfig,
    CustomerHTTPProvider,
    CustomerQueryService,
    ReadOnlyCustomerProvider,
)
from app.domain.business_tools.models import (
    BusinessQueryContext,
    OrderQueryInput,
    LogisticsQueryInput,
)
from app.domain.business_tools.providers import (
    BusinessQueryAuditSink,
    ReadOnlyBusinessProvider,
)
from app.domain.business_tools.service import (
    BusinessQueryService,
    BusinessQueryAccessDenied,
    BusinessQueryTimeout,
    BusinessQueryAuditError,
    BusinessQueryResultError,
    BusinessQueryProviderError,
)
from app.domain.business_tools.target_transport import business_query_log_scope
from app.domain.business_tools.trial_runner import SAFE_SUMMARY_LABELS
from app.schemas.agent_business_execution import (
    BusinessExecutionCommand,
    BusinessExecutionCheck,
    BusinessExecutionReceipt,
)
from app.schemas.business_execution_resolution import BusinessExecutionResolution
from app.schemas.connection_test_protocol import ConnectionTestSummary, ErrorCode
from app.services.business_execution_owner_client import BusinessExecutionOwnerClient
from app.services.business_connection_client import ConnectionAuthorityUnavailable


class Owner(Protocol):
    async def resolve(
        self, check: BusinessExecutionCheck
    ) -> BusinessExecutionResolution: ...


class BusinessExecutionRunner:
    def __init__(
        self, owner: Owner | None = None, *, audit: BusinessQueryAuditSink | None = None
    ):
        self.owner = owner or BusinessExecutionOwnerClient()
        self.audit = audit or DatabaseBusinessQueryAuditSink()

    def provider(
        self, resolution: BusinessExecutionResolution
    ) -> ReadOnlyBusinessProvider:
        return cast(
            ReadOnlyBusinessProvider,
            ConnectionTestRunner(audit=self.audit)._provider(resolution.snapshot),
        )

    def customer_provider(
        self, resolution: BusinessExecutionResolution
    ) -> ReadOnlyCustomerProvider:
        configuration = resolution.snapshot.connection.configuration
        if configuration is None:
            raise ConnectionAuthorityUnavailable()
        try:
            customer_config = CustomerHTTPConfig.from_business_connection(
                configuration, resolution.snapshot.credentials
            )
        except ValueError:
            raise ConnectionAuthorityUnavailable() from None
        return CustomerHTTPProvider(customer_config)

    @staticmethod
    def safe_label(
        value: str,
        command: BusinessExecutionCommand,
        resolution: BusinessExecutionResolution,
    ) -> str:
        forbidden = [
            command.order_no.get_secret_value() if command.order_no else "",
            command.context.context_token.get_secret_value(),
            resolution.customer_id.get_secret_value(),
        ]
        if resolution.snapshot.credentials:
            for item in (
                resolution.snapshot.credentials.token,
                resolution.snapshot.credentials.username,
                resolution.snapshot.credentials.password,
            ):
                if item is not None:
                    forbidden.append(item.get_secret_value())
        if any(private and private in value for private in forbidden):
            return "结果标签需配置映射"
        value = value.strip()
        return (
            value
            if value in SAFE_SUMMARY_LABELS
            else value.lower()
            if value.lower() in SAFE_SUMMARY_LABELS
            else "结果标签需配置映射"
        )

    @staticmethod
    def safe_customer_label(
        value: str,
        command: BusinessExecutionCommand,
        resolution: BusinessExecutionResolution,
    ) -> str:
        forbidden = [
            command.context.context_token.get_secret_value(),
            resolution.customer_id.get_secret_value(),
            str(command.context.visitor_id),
        ]
        if resolution.snapshot.credentials:
            for item in (
                resolution.snapshot.credentials.token,
                resolution.snapshot.credentials.username,
                resolution.snapshot.credentials.password,
            ):
                if item is not None:
                    forbidden.append(item.get_secret_value())
        if any(private and private in value for private in forbidden) or re.search(
            r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\d{7,}|https?://|\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b",
            value,
        ):
            return "结果标签需配置映射"
        return value

    async def run(self, command: BusinessExecutionCommand) -> BusinessExecutionReceipt:
        original = await self.owner.resolve(
            BusinessExecutionCheck(command=command, phase="before_query")
        )
        connection = original.snapshot.connection
        token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        if (
            not connection.enabled
            or connection.mode != "http"
            or command.operation not in connection.capabilities
            or connection.configuration is None
            or token is None
            or len(token.get_secret_value()) < 32
        ):
            raise ConnectionAuthorityUnavailable()
        context = BusinessQueryContext(
            tenant_id=command.reference.project_id,
            visitor_id=str(command.context.visitor_id),
            external_customer_id=original.customer_id.get_secret_value(),
            conversation_id=str(original.principal.visitor_session_id),
            request_id=str(command.query_id),
            actor_id=str(command.reference.agent_id),
            source_message_id=original.principal.source_message_id,
            response_client_msg_no=original.principal.response_client_msg_no,
        )
        summary: ConnectionTestSummary | None = None
        error: ErrorCode | None = None
        try:
            with business_query_log_scope():
                if command.operation == "order_query":
                    if command.order_no is None:
                        raise ConnectionAuthorityUnavailable()
                    service = BusinessQueryService(
                        self.provider(original), self.audit,
                        timeout_seconds=connection.configuration.timeout_seconds,
                        audit_fingerprint_key=token.get_secret_value().encode(),
                    )
                    order = await service.query_order(
                        OrderQueryInput(order_no=command.order_no.get_secret_value()),
                        context,
                    )
                    summary = ConnectionTestSummary(
                        status=self.safe_label(order.status, command, original),
                        amount_minor=order.amount_minor,
                        currency=order.currency,
                    )
                elif command.operation == "logistics_query":
                    if command.order_no is None:
                        raise ConnectionAuthorityUnavailable()
                    service = BusinessQueryService(
                        self.provider(original), self.audit,
                        timeout_seconds=connection.configuration.timeout_seconds,
                        audit_fingerprint_key=token.get_secret_value().encode(),
                    )
                    logistics = await service.query_logistics(
                        LogisticsQueryInput(
                            order_no=command.order_no.get_secret_value()
                        ),
                        context,
                    )
                    summary = ConnectionTestSummary(
                        status=self.safe_label(logistics.status, command, original),
                        carrier=self.safe_label(logistics.carrier, command, original),
                        tracking_no_masked=logistics.tracking_no_masked,
                    )
                else:
                    customer_service = CustomerQueryService(
                        self.customer_provider(original), self.audit,
                        timeout_seconds=connection.configuration.timeout_seconds,
                        audit_fingerprint_key=token.get_secret_value().encode(),
                    )
                    if command.operation == "customer_profile_query":
                        profile = await customer_service.query_profile(context)
                        summary = ConnectionTestSummary(
                            status="查询完成",
                            segment=self.safe_customer_label(profile.segment, command, original)
                            if profile.segment else None,
                            service_tags=[
                                self.safe_customer_label(tag, command, original)
                                for tag in profile.service_tags
                            ],
                        )
                    else:
                        membership = await customer_service.query_membership(context)
                        summary = ConnectionTestSummary(
                            status="查询完成",
                            tier=self.safe_customer_label(membership.tier, command, original),
                            points_balance=membership.points_balance,
                        )
            if summary.status == "结果标签需配置映射" or (
                command.operation == "customer_profile_query"
                and (summary.segment == "结果标签需配置映射"
                     or "结果标签需配置映射" in (summary.service_tags or []))
            ) or (
                command.operation == "membership_query"
                and summary.tier == "结果标签需配置映射"
            ):
                summary, error = None, "business_failed"
        except BusinessQueryAccessDenied:
            error = "denied"
        except BusinessQueryTimeout:
            error = "timeout"
        except BusinessQueryAuditError:
            error = "audit_unavailable"
        except BusinessQueryResultError as exc:
            error = exc.code
        except BusinessQueryProviderError:
            error = "provider_failed"
        current = await self.owner.resolve(
            BusinessExecutionCheck(command=command, phase="after_query")
        )
        if (
            current.snapshot != original.snapshot
            or current.customer_id != original.customer_id
            or current.principal
            != original.principal.model_copy(
                update={"checked_at": current.principal.checked_at}
            )
            or current.authority.model_dump(exclude={"phase", "checked_at"})
            != original.authority.model_dump(exclude={"phase", "checked_at"})
        ):
            raise ConnectionAuthorityUnavailable()
        return BusinessExecutionReceipt.model_validate(
            {
                **command.identity(),
                "authority": current.authority,
                "outcome": "failed" if error else "passed",
                "summary": summary,
                "error_code": error,
                "checked_at": datetime.now(UTC),
            }
        )

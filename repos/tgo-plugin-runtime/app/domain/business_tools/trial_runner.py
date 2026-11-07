"""Employee tests query only verified customers, without a production grant."""

from datetime import UTC, datetime
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
from app.schemas.agent_business_trials import (
    BusinessTrialCommand,
    BusinessTrialCheck,
    BusinessTrialReceipt,
)
from app.schemas.connection_test_protocol import (
    ConnectionTestSummary,
    ErrorCode,
)
from app.schemas.business_trial_resolution import BusinessTrialResolution
from app.services.business_connection_client import (
    ConnectionAuthorityUnavailable,
)
from app.services.business_trial_owner_client import BusinessTrialOwnerClient


class Owner(Protocol):
    async def resolve(
        self, check: BusinessTrialCheck
    ) -> BusinessTrialResolution:
        ...


SAFE_SUMMARY_LABELS = frozenset(
    {
        "已发货",
        "未发货",
        "待发货",
        "待付款",
        "已付款",
        "已支付",
        "已签收",
        "已完成",
        "已取消",
        "处理中",
        "运输中",
        "配送中",
        "派送中",
        "已揽收",
        "物流异常",
        "查询完成",
        "pending",
        "paid",
        "shipped",
        "delivered",
        "cancelled",
        "completed",
        "processing",
        "in_transit",
        "out_for_delivery",
        "顺丰",
        "顺丰速运",
        "中通",
        "中通快递",
        "圆通",
        "圆通速递",
        "申通",
        "申通快递",
        "韵达",
        "韵达快递",
        "京东",
        "京东物流",
        "邮政",
        "中国邮政",
        "EMS",
        "极兔",
        "极兔速递",
        "德邦",
        "德邦快递",
        "DHL",
        "UPS",
        "FedEx",
    }
)


class BusinessTrialRunner:
    def __init__(
        self,
        owner: Owner | None = None,
        *,
        audit: BusinessQueryAuditSink | None = None,
    ):
        self.owner = owner or BusinessTrialOwnerClient()
        self.audit = audit or DatabaseBusinessQueryAuditSink()

    def provider(
        self, value: BusinessTrialResolution
    ) -> ReadOnlyBusinessProvider:
        # The same restricted HTTP transport used by configuration tests. Demo
        # must be rejected before this factory can ever be invoked.
        return cast(
            ReadOnlyBusinessProvider,
            ConnectionTestRunner(audit=self.audit)._provider(value.snapshot),
        )

    def customer_provider(
        self, value: BusinessTrialResolution
    ) -> ReadOnlyCustomerProvider:
        configuration = value.snapshot.connection.configuration
        if configuration is None:
            raise ConnectionAuthorityUnavailable()
        try:
            customer_config = CustomerHTTPConfig.from_business_connection(
                configuration, value.snapshot.credentials
            )
        except ValueError:
            raise ConnectionAuthorityUnavailable() from None
        return CustomerHTTPProvider(customer_config)

    @staticmethod
    def safe_text(
        value: str,
        command: BusinessTrialCommand,
        resolution: BusinessTrialResolution,
    ) -> str:
        from re import sub

        secrets = [
            command.order_no.get_secret_value() if command.order_no else "",
            command.customer_session.get_secret_value(),
            resolution.customer_id.get_secret_value(),
        ]
        if resolution.snapshot.credentials:
            for item in (
                resolution.snapshot.credentials.token,
                resolution.snapshot.credentials.username,
                resolution.snapshot.credentials.password,
            ):
                if item is not None:
                    secrets.append(item.get_secret_value())
        for private in secrets:
            if private:
                value = value.replace(private, "[已隐藏]")
        value = sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[已隐藏]", value)
        value = sub(r"\d{7,}", "[已隐藏]", value)
        # Upstream free text may contain names/addresses even in a status
        # field.
        # Store only recognized business labels, never that arbitrary text.
        value = value.strip()
        normalized = value if value in SAFE_SUMMARY_LABELS else value.lower()
        return normalized if normalized in SAFE_SUMMARY_LABELS else "结果标签需配置映射"

    async def run(self, command: BusinessTrialCommand) -> BusinessTrialReceipt:
        original = await self.owner.resolve(
            BusinessTrialCheck(command=command, phase="before_query")
        )
        if (
            original.snapshot.connection.mode != "http"
            or not original.snapshot.connection.enabled
            or command.operation
            not in original.snapshot.connection.capabilities
        ):
            raise ConnectionAuthorityUnavailable()
        key = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        configuration = original.snapshot.connection.configuration
        if (
            key is None
            or len(key.get_secret_value()) < 32
            or configuration is None
        ):
            raise ConnectionAuthorityUnavailable()
        context = BusinessQueryContext(
            tenant_id=command.project_id,
            visitor_id=str(command.visitor_id),
            external_customer_id=original.customer_id.get_secret_value(),
            conversation_id="business-test-" + str(command.report_id),
            request_id=str(command.request_id),
            actor_id=str(command.actor_id),
        )
        summary: ConnectionTestSummary | None = None
        error: ErrorCode | None = None
        try:
            with business_query_log_scope():
                if command.operation == "order_query":
                    if command.order_no is None:
                        raise ConnectionAuthorityUnavailable()
                    service = BusinessQueryService(
                        self.provider(original),
                        self.audit,
                        timeout_seconds=configuration.timeout_seconds,
                        audit_fingerprint_key=key.get_secret_value().encode(),
                    )
                    order = await service.query_order(
                        OrderQueryInput(
                            order_no=command.order_no.get_secret_value()
                        ),
                        context,
                    )
                    summary = ConnectionTestSummary(
                        status=self.safe_text(order.status, command, original),
                        amount_minor=order.amount_minor,
                        currency=order.currency,
                    )
                elif command.operation == "logistics_query":
                    if command.order_no is None:
                        raise ConnectionAuthorityUnavailable()
                    service = BusinessQueryService(
                        self.provider(original),
                        self.audit,
                        timeout_seconds=configuration.timeout_seconds,
                        audit_fingerprint_key=key.get_secret_value().encode(),
                    )
                    logistics = await service.query_logistics(
                        LogisticsQueryInput(
                            order_no=command.order_no.get_secret_value()
                        ),
                        context,
                    )
                    summary = ConnectionTestSummary(
                        status=self.safe_text(
                            logistics.status, command, original
                        ),
                        carrier=self.safe_text(
                            logistics.carrier, command, original
                        ),
                        tracking_no_masked=logistics.tracking_no_masked,
                    )
                else:
                    customer_service = CustomerQueryService(
                        self.customer_provider(original),
                        self.audit,
                        timeout_seconds=configuration.timeout_seconds,
                        audit_fingerprint_key=key.get_secret_value().encode(),
                    )
                    if command.operation == "customer_profile_query":
                        profile = await customer_service.query_profile(context)
                        summary = ConnectionTestSummary(
                            status="查询完成",
                            segment=self.safe_customer_label(
                                profile.segment, command, original
                            )
                            if profile.segment
                            else None,
                            service_tags=[
                                self.safe_customer_label(
                                    tag, command, original
                                )
                                for tag in profile.service_tags
                            ],
                        )
                    else:
                        membership = await customer_service.query_membership(
                            context
                        )
                        summary = ConnectionTestSummary(
                            status="查询完成",
                            tier=self.safe_customer_label(
                                membership.tier, command, original
                            ),
                            points_balance=membership.points_balance,
                        )
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
            BusinessTrialCheck(command=command, phase="after_query")
        )
        if (
            current.snapshot != original.snapshot
            or current.customer_evidence != original.customer_evidence
            or current.customer_id != original.customer_id
        ):
            raise ConnectionAuthorityUnavailable()
        return BusinessTrialReceipt.model_validate(
            {
                **command.identity(),
                "outcome": "failed" if error else "passed",
                "summary": summary,
                "error_code": error,
                "customer_evidence": current.customer_evidence,
                "checked_at": datetime.now(UTC),
            }
        )

    @staticmethod
    def safe_customer_label(
        value: str,
        command: BusinessTrialCommand,
        resolution: BusinessTrialResolution,
    ) -> str:
        from re import search

        private = [
            command.customer_session.get_secret_value(),
            resolution.customer_id.get_secret_value(),
        ]
        if command.order_no:
            private.append(command.order_no.get_secret_value())
        if resolution.snapshot.credentials:
            for item in (
                resolution.snapshot.credentials.token,
                resolution.snapshot.credentials.username,
                resolution.snapshot.credentials.password,
            ):
                if item:
                    private.append(item.get_secret_value())
        private.append(str(command.visitor_id))
        if any(secret and secret in value for secret in private) or search(
            (
                "[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}|\\d{7,}|https?://|\\b[0-9a-"
                "f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\\b"
            ),
            value,
        ):
            return "结果标签需配置映射"
        return value

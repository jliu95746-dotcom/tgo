"""One test is checked before and after the same audited read operation."""

from datetime import datetime, timezone
from typing import Protocol

from app.domain.business_tools.audit_sink import DatabaseBusinessQueryAuditSink
from app.domain.business_tools.demo_provider import DemoHandbagBusinessProvider
from app.domain.business_tools.customer_tools import (
    CustomerHTTPConfig,
    CustomerHTTPProvider,
    CustomerQueryService,
)
from app.domain.business_tools.http_provider import (
    HTTPBusinessProvider,
    HTTPBusinessProviderConfig,
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
    BusinessQueryProviderError,
    BusinessQueryResultError,
    BusinessQueryAuditError,
)
from app.domain.business_tools.target_transport import (
    PinnedBusinessTransport,
    business_query_log_scope,
)
from app.schemas.business_connections import BusinessConnectionPrivateSnapshot
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
    ConnectionTestSummary,
    ErrorCode,
)
from app.services.business_connection_client import (
    BusinessConnectionOwnerClient,
    ConnectionAuthorityUnavailable,
)
from app.config import settings


class OwnerReader(Protocol):
    async def resolve(
        self, command: ConnectionTestCommand
    ) -> BusinessConnectionPrivateSnapshot:
        ...


class ConnectionTestRunner:
    @staticmethod
    def _summary_text(value: str, command: ConnectionTestCommand) -> str:
        for private_input in (command.order_no, command.customer_id):
            if private_input:
                value = value.replace(private_input, "[已隐藏]")
        return value[:128]

    def __init__(
        self,
        owner: OwnerReader | None = None,
        *,
        audit: BusinessQueryAuditSink | None = None,
    ):
        self.owner = owner or BusinessConnectionOwnerClient()
        self.audit = audit or DatabaseBusinessQueryAuditSink()

    def _provider(
        self, snapshot: BusinessConnectionPrivateSnapshot
    ) -> ReadOnlyBusinessProvider:
        if snapshot.connection.mode == "demo":
            return DemoHandbagBusinessProvider()
        configuration, credentials = (
            snapshot.connection.configuration,
            snapshot.credentials,
        )
        if configuration is None:
            raise ConnectionAuthorityUnavailable()
        config = HTTPBusinessProviderConfig(
            **configuration.model_dump(),
            auth_token=credentials.token.get_secret_value()
            if credentials and credentials.token
            else None,
            basic_username=credentials.username.get_secret_value()
            if credentials and credentials.username
            else None,
            basic_password=credentials.password.get_secret_value()
            if credentials and credentials.password
            else None,
        )
        transport = PinnedBusinessTransport(
            configuration.base_url,
            [configuration.order_path, configuration.logistics_path],
        )
        return HTTPBusinessProvider(config, transport=transport)

    async def run(self, command: ConnectionTestCommand) -> ConnectionTestReceipt:
        original = await self.owner.resolve(command)
        connection = original.connection
        key = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
        if key is None or len(key.get_secret_value()) < 32:
            raise ConnectionAuthorityUnavailable()
        customer_operation = command.operation in {
            "customer_profile_query", "membership_query"
        }
        if customer_operation:
            if connection.mode != "http" or connection.configuration is None:
                raise ConnectionAuthorityUnavailable()
            try:
                customer_config = CustomerHTTPConfig.from_business_connection(
                    connection.configuration, original.credentials
                )
            except ValueError:
                raise ConnectionAuthorityUnavailable() from None
            customer_service = CustomerQueryService(
                CustomerHTTPProvider(customer_config),
                self.audit,
                timeout_seconds=customer_config.timeout_seconds,
                audit_fingerprint_key=key.get_secret_value().encode(),
            )
        else:
            service = BusinessQueryService(
                self._provider(original),
                self.audit,
                timeout_seconds=connection.configuration.timeout_seconds
                if connection.configuration
                else 5,
                audit_fingerprint_key=key.get_secret_value().encode(),
            )
        context = BusinessQueryContext(
            tenant_id=command.project_id,
            visitor_id="connection-test-" + str(command.actor_id),
            external_customer_id=command.customer_id,
            conversation_id="connection-test-" + str(command.request_id),
            request_id=str(command.request_id),
            actor_id=str(command.actor_id),
        )
        summary = None
        error: ErrorCode | None = None
        try:
            with business_query_log_scope():
                if command.operation == "order_query":
                    assert command.order_no is not None
                    result = await service.query_order(
                        OrderQueryInput(order_no=command.order_no), context
                    )
                    summary = ConnectionTestSummary(
                        status=self._summary_text(result.status, command),
                        amount_minor=result.amount_minor,
                        currency=result.currency,
                    )
                elif command.operation == "logistics_query":
                    assert command.order_no is not None
                    logistics = await service.query_logistics(
                        LogisticsQueryInput(order_no=command.order_no), context
                    )
                    summary = ConnectionTestSummary(
                        status=self._summary_text(logistics.status, command),
                        carrier=self._summary_text(logistics.carrier, command),
                        tracking_no_masked=logistics.tracking_no_masked,
                    )
                elif command.operation == "customer_profile_query":
                    profile = await customer_service.query_profile(context)
                    summary = ConnectionTestSummary(
                        status="查询成功",
                        segment=self._summary_text(profile.segment, command)
                        if profile.segment else None,
                        service_tags=[
                            self._summary_text(tag, command)
                            for tag in profile.service_tags
                        ],
                    )
                else:
                    membership = await customer_service.query_membership(context)
                    summary = ConnectionTestSummary(
                        status="查询成功",
                        tier=self._summary_text(membership.tier, command),
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
        current = await self.owner.resolve(command)
        if current.connection != connection:
            # Even a late rename is not a receipt for the original saved record.
            raise ConnectionAuthorityUnavailable()
        return ConnectionTestReceipt(
            scope="business_connection_test",
            can_enable=False,
            **command.model_dump(exclude={"order_no", "customer_id"}),
            mode=connection.mode,
            outcome="failed"
            if error
            else "demo"
            if connection.mode == "demo"
            else "passed",
            summary=summary,
            error_code=error,
            checked_at=datetime.now(timezone.utc),
        )

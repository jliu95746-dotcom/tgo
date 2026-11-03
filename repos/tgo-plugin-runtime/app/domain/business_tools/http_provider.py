"""Configurable HTTP adapter for read-only order and logistics systems."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.domain.business_tools.models import (
    BusinessQueryContext,
    LogisticsQueryInput,
    LogisticsQueryResult,
    OrderOwnership,
    OrderQueryInput,
    OrderQueryResult,
    OwnedLogisticsQueryResult,
    OwnedOrderQueryResult,
)
from app.domain.business_tools.providers import (
    BusinessProviderAccessDenied,
    BusinessProviderResultError,
)
from app.domain.business_tools.merchant_time import normalize_merchant_time
from app.domain.business_tools.target_transport import safe_business_response
from app.schemas.business_connections import BusinessMerchantMapping

HTTPMethod = Literal["GET", "POST"]
AuthMode = Literal["none", "bearer", "api_key", "basic"]
AmountUnit = Literal["minor", "major"]

_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
_TRACKING_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True, slots=True)
class HTTPBusinessProviderConfig:
    """Validated settings for one normalized read-only business API."""

    base_url: str
    order_path: str
    logistics_path: str
    customer_profile_path: str | None = None
    membership_path: str | None = None
    method: HTTPMethod = "POST"
    timeout_seconds: float = 5.0
    auth_mode: AuthMode = "none"
    auth_token: str | None = field(default=None, repr=False)
    api_key_header: str = "X-API-Key"
    basic_username: str | None = field(default=None, repr=False)
    basic_password: str | None = field(default=None, repr=False)
    data_path: str = "data"
    success_field: str | None = None
    success_value: str = "0"
    tenant_id_field: str = "tenant_id"
    customer_id_field: str = "customer_id"
    order_no_field: str = "order_no"
    order_status_field: str = "status"
    order_amount_field: str = "amount_minor"
    order_amount_unit: AmountUnit = "minor"
    order_currency_field: str = "currency"
    order_created_at_field: str = "created_at"
    logistics_status_field: str = "status"
    logistics_carrier_field: str = "carrier"
    logistics_tracking_no_field: str = "tracking_no"
    logistics_updated_at_field: str = "updated_at"
    customer_segment_field: str = "segment"
    customer_tags_field: str = "service_tags"
    membership_tier_field: str = "tier"
    membership_points_field: str = "points_balance"
    merchant_id: str | None = None
    request_order_no_field: str = "order_no"
    request_customer_id_field: str = "customer_id"
    request_merchant_id_field: str = "tenant_id"
    request_operation_field: str | None = "operation"
    order_operation_value: str = "order_query"
    logistics_operation_value: str = "logistics_query"
    order_status_map: dict[str, str] = field(default_factory=dict)
    logistics_status_map: dict[str, str] = field(default_factory=dict)
    not_found_values: list[str] = field(default_factory=list)
    logistics_not_shipped_values: list[str] = field(default_factory=list)
    default_timezone: str = "UTC"

    def __post_init__(self) -> None:
        # Validate saved mapping semantics even for direct, legacy construction.
        names = (
            "merchant_id",
            "request_order_no_field",
            "request_customer_id_field",
            "request_merchant_id_field",
            "request_operation_field",
            "order_operation_value",
            "logistics_operation_value",
            "order_status_map",
            "logistics_status_map",
            "not_found_values",
            "logistics_not_shipped_values",
            "default_timezone",
        )
        BusinessMerchantMapping(
            success_field=self.success_field,
            success_value=self.success_value,
            **{name: getattr(self, name) for name in names},
        )
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "base_url must be an http or https URL without credentials, "
                "query, or fragment"
            )
        for field_name, path in (
            ("order_path", self.order_path),
            ("logistics_path", self.logistics_path),
        ):
            if (
                not path.startswith("/")
                or path.startswith("//")
                or "?" in path
                or "#" in path
                or ".." in path.split("/")
            ):
                raise ValueError(f"{field_name} must be a safe absolute API path")
        customer_paths = (self.customer_profile_path, self.membership_path)
        if (customer_paths[0] is None) != (customer_paths[1] is None):
            raise ValueError("customer query paths must be configured together")
        if customer_paths[0] is not None:
            for customer_path in customer_paths:
                if customer_path is None or (
                    not customer_path.startswith("/")
                    or customer_path.startswith("//")
                    or "?" in customer_path
                    or "#" in customer_path
                    or ".." in customer_path.split("/")
                ):
                    raise ValueError("customer query path must be a safe absolute API path")
            if len(set((self.order_path, self.logistics_path, *customer_paths))) != 4:
                raise ValueError("business query paths must be distinct")
        if self.method not in {"GET", "POST"}:
            raise ValueError("method must be GET or POST")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if self.auth_mode in {"bearer", "api_key"} and not self.auth_token:
            raise ValueError(f"auth_token is required for {self.auth_mode} auth")
        if self.auth_mode == "api_key" and not _HEADER_NAME_PATTERN.fullmatch(
            self.api_key_header
        ):
            raise ValueError("api_key_header is invalid")
        if self.auth_mode == "basic" and (
            not self.basic_username or not self.basic_password
        ):
            raise ValueError(
                "basic_username and basic_password are required for basic auth"
            )


class HTTPBusinessProvider:
    """Query a customer-owned external API without exposing write operations."""

    def __init__(
        self,
        config: HTTPBusinessProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport

    async def query_order(
        self,
        query: OrderQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedOrderQueryResult:
        data = await self._query(
            path=self._config.order_path,
            operation="order_query",
            order_no=query.order_no,
            context=context,
        )
        ownership = self._ownership(data, query.order_no, context)
        amount = self._optional_amount(
            self._optional(data, self._config.order_amount_field)
        )
        return OwnedOrderQueryResult(
            ownership=ownership,
            result=OrderQueryResult(
                order_no=self._required_text(
                    data,
                    self._config.order_no_field,
                ),
                status=self._status(
                    data, self._config.order_status_field, self._config.order_status_map
                ),
                amount_minor=amount,
                currency=self._optional_text(
                    data,
                    self._config.order_currency_field,
                ),
                created_at=self._optional_datetime(
                    data,
                    self._config.order_created_at_field,
                ),
            ),
        )

    async def query_logistics(
        self,
        query: LogisticsQueryInput,
        context: BusinessQueryContext,
    ) -> OwnedLogisticsQueryResult:
        data = await self._query(
            path=self._config.logistics_path,
            operation="logistics_query",
            order_no=query.order_no,
            context=context,
        )
        ownership = self._ownership(data, query.order_no, context)
        if (
            self._required_text(data, self._config.logistics_status_field)
            in self._config.logistics_not_shipped_values
        ):
            raise BusinessProviderResultError("not_shipped")
        tracking_no = self._required_text(
            data,
            self._config.logistics_tracking_no_field,
        )
        return OwnedLogisticsQueryResult(
            ownership=ownership,
            result=LogisticsQueryResult(
                order_no=self._required_text(
                    data,
                    self._config.order_no_field,
                ),
                status=self._status(
                    data,
                    self._config.logistics_status_field,
                    self._config.logistics_status_map,
                ),
                carrier=self._required_text(
                    data,
                    self._config.logistics_carrier_field,
                ),
                tracking_no_masked=self._mask_tracking_number(tracking_no),
                updated_at=self._optional_datetime(
                    data,
                    self._config.logistics_updated_at_field,
                ),
            ),
        )

    async def _query(
        self,
        *,
        path: str,
        operation: str,
        order_no: str,
        context: BusinessQueryContext,
    ) -> dict[str, object]:
        if not context.external_customer_id:
            raise ValueError(
                "external_customer_id is required for an HTTP business query"
            )
        payload = {
            self._config.request_order_no_field: order_no,
            self._config.request_merchant_id_field: self._config.merchant_id
            or str(context.tenant_id),
            self._config.request_customer_id_field: context.external_customer_id,
        }
        if self._config.request_operation_field is not None:
            payload[self._config.request_operation_field] = (
                self._config.order_operation_value
                if operation == "order_query"
                else self._config.logistics_operation_value
            )
        headers = {
            "Accept": "application/json",
            "X-TGO-Tenant-ID": str(context.tenant_id),
            "X-TGO-Visitor-ID": context.visitor_id,
            "X-TGO-Request-ID": context.request_id,
        }
        auth: httpx.Auth | None = None
        if self._config.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        elif self._config.auth_mode == "api_key":
            headers[self._config.api_key_header] = str(self._config.auth_token)
        elif self._config.auth_mode == "basic":
            auth = httpx.BasicAuth(
                self._config.basic_username or "",
                self._config.basic_password or "",
            )

        async with httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await safe_business_response(
                client.request(
                    self._config.method,
                    self._url(path),
                    headers=headers,
                    auth=auth,
                    params=payload if self._config.method == "GET" else None,
                    json=payload if self._config.method == "POST" else None,
                )
            )
        if response.status_code in {401, 403}:
            raise BusinessProviderResultError("authentication_failed")
        if not 200 <= response.status_code < 300:
            raise RuntimeError("business provider returned HTTP error")
        decoded = response.json()
        if not isinstance(decoded, dict):
            raise ValueError("upstream response must be a JSON object")
        if self._config.success_field:
            actual = self._required(decoded, self._config.success_field)
            if not isinstance(actual, (str, int, float, bool)):
                raise ValueError("upstream business outcome must be a scalar")
            code = str(actual)
            if code in self._config.not_found_values:
                raise BusinessProviderResultError("not_found")
            if str(actual) != self._config.success_value:
                raise BusinessProviderResultError("business_failed")
        data = self._required(decoded, self._config.data_path)
        if not isinstance(data, dict):
            raise ValueError("configured upstream data must be a JSON object")
        return data

    def _ownership(
        self,
        data: dict[str, object],
        requested_order_no: str,
        context: BusinessQueryContext,
    ) -> OrderOwnership:
        upstream_customer_id = self._required_text(
            data,
            self._config.customer_id_field,
        )
        if upstream_customer_id != context.external_customer_id:
            raise BusinessProviderAccessDenied("customer ownership mismatch")
        returned_order = self._required_text(data, self._config.order_no_field)
        returned_merchant = self._required_text(data, self._config.tenant_id_field)
        if returned_order != requested_order_no or returned_merchant != (
            self._config.merchant_id or str(context.tenant_id)
        ):
            raise BusinessProviderAccessDenied("merchant or order ownership mismatch")
        return OrderOwnership(
            tenant_id=context.tenant_id,
            visitor_id=context.visitor_id,
            order_no=self._required_text(
                data,
                self._config.order_no_field,
            ),
        )

    def _optional_amount(self, value: object | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("configured amount field must be numeric")
        try:
            decimal_value = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("configured amount field must be numeric") from exc
        if decimal_value < 0:
            raise ValueError("configured amount field must not be negative")
        if self._config.order_amount_unit == "major":
            decimal_value *= 100
        integral = decimal_value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if decimal_value != integral:
            raise ValueError("configured amount field has unsupported precision")
        return int(integral)

    def _optional_datetime(
        self,
        data: dict[str, object],
        path: str,
    ) -> datetime | None:
        value = HTTPBusinessProvider._optional(data, path)
        if value is None:
            return None
        if not isinstance(value, (str, datetime)):
            raise ValueError(f"configured field {path!r} must be an ISO datetime")
        try:
            return normalize_merchant_time(value, self._config.default_timezone)
        except ValueError as exc:
            raise ValueError(
                f"configured field {path!r} must be an ISO datetime"
            ) from exc

    @staticmethod
    def _mask_tracking_number(value: str) -> str:
        if "*" in value:
            if re.fullmatch(r"[A-Za-z0-9-]{0,16}\*{2,}[A-Za-z0-9*-]{0,16}", value):
                return value
            raise ValueError("masked tracking number has an invalid format")
        if not _TRACKING_NUMBER_PATTERN.fullmatch(value):
            raise ValueError("tracking number contains unsupported characters")
        if len(value) <= 4:
            return f"{value[:1]}**{value[-1:]}"
        prefix_length = 2 if len(value) < 10 else 2
        suffix_length = min(4, len(value) - prefix_length - 2)
        stars = "*" * max(2, len(value) - prefix_length - suffix_length)
        return f"{value[:prefix_length]}{stars}{value[-suffix_length:]}"

    def _url(self, path: str) -> str:
        return f"{self._config.base_url.rstrip('/')}{path}"

    def _status(
        self, data: dict[str, object], path: str, mapping: dict[str, str]
    ) -> str:
        raw = self._required_text(data, path)
        if not mapping:
            return raw
        if raw not in mapping:
            raise ValueError("upstream status is not mapped")
        return mapping[raw]

    @staticmethod
    def _required_text(data: dict[str, object], path: str) -> str:
        value = HTTPBusinessProvider._required(data, path)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"configured field {path!r} must be text")
        normalized = str(value).strip()
        if not normalized:
            raise ValueError(f"configured field {path!r} must not be empty")
        return normalized

    @staticmethod
    def _required_uuid(data: dict[str, object], path: str) -> UUID:
        value = HTTPBusinessProvider._required_text(data, path)
        try:
            return UUID(value)
        except ValueError as exc:
            raise ValueError(f"configured field {path!r} must be a UUID") from exc

    @staticmethod
    def _optional_text(data: dict[str, object], path: str) -> str | None:
        value = HTTPBusinessProvider._optional(data, path)
        if value is None:
            return None
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise ValueError(f"configured field {path!r} must be text")
        normalized = str(value).strip()
        return normalized or None

    @staticmethod
    def _required(data: dict[str, object], path: str) -> object:
        value = HTTPBusinessProvider._optional(data, path)
        if value is None:
            raise ValueError(f"configured field {path!r} is missing")
        return value

    @staticmethod
    def _optional(data: dict[str, object], path: str) -> object | None:
        current: object = data
        if not path:
            return current
        for segment in path.split("."):
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
        return current

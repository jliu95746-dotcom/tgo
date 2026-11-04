"""Minimal read-only customer profile and membership query boundary.

The caller supplies only a trusted conversation context. It cannot select an
arbitrary customer, and the upstream must prove tenant and customer ownership.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import re
from time import monotonic
from typing import Literal, Protocol, cast
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.business_tools.models import (
    BusinessQueryAuditEvent,
    BusinessQueryContext,
    BusinessQueryOperation,
    BusinessQueryOutcome,
    Identifier,
    ShortBusinessText,
    message_lookup_digest,
)
from app.domain.business_tools.providers import (
    BusinessProviderAccessDenied,
    BusinessQueryAuditSink,
)
from app.domain.business_tools.service import (
    BusinessQueryAccessDenied,
    BusinessQueryAuditError,
    BusinessQueryProviderError,
    BusinessQueryTimeout,
)
from app.domain.business_tools.target_transport import (
    PinnedBusinessTransport,
    safe_business_response,
)
from app.schemas.business_connections import (
    BusinessConnectionCredentials,
    BusinessHTTPConfiguration,
)


class StrictCustomerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_CONTACT_PATTERN = re.compile(
    r"(?:[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|\+?\d[\d -]{8,}\d|https?://)",
    re.IGNORECASE,
)


def _safe_label(value: str) -> str:
    if _CONTACT_PATTERN.search(value):
        raise ValueError("contact details cannot be returned as business labels")
    return value


class CustomerOwnership(StrictCustomerModel):
    tenant_id: UUID
    visitor_id: Identifier
    external_customer_id: Identifier


class CustomerProfileResult(StrictCustomerModel):
    """Only service-safe labels; contact details and private notes are excluded."""

    segment: ShortBusinessText | None = None
    service_tags: list[ShortBusinessText] = Field(default_factory=list, max_length=8)

    @field_validator("segment")
    @classmethod
    def safe_segment(cls, value: str | None) -> str | None:
        return _safe_label(value) if value is not None else None

    @field_validator("service_tags")
    @classmethod
    def safe_service_tags(cls, values: list[str]) -> list[str]:
        return [_safe_label(value) for value in values]


class MembershipResult(StrictCustomerModel):
    tier: ShortBusinessText
    points_balance: int = Field(ge=0)

    @field_validator("tier")
    @classmethod
    def safe_tier(cls, value: str) -> str:
        return _safe_label(value)


class OwnedCustomerProfile(StrictCustomerModel):
    ownership: CustomerOwnership
    result: CustomerProfileResult


class OwnedMembership(StrictCustomerModel):
    ownership: CustomerOwnership
    result: MembershipResult


class ReadOnlyCustomerProvider(Protocol):
    async def query_profile(
        self, context: BusinessQueryContext
    ) -> OwnedCustomerProfile:
        ...

    async def query_membership(self, context: BusinessQueryContext) -> OwnedMembership:
        ...


@dataclass(frozen=True, slots=True)
class CustomerHTTPConfig:
    base_url: str
    profile_path: str
    membership_path: str
    timeout_seconds: float = 5.0
    auth_mode: Literal["none", "bearer", "api_key", "basic"] = "none"
    auth_token: str | None = field(default=None, repr=False)
    api_key_header: str = "X-API-Key"
    basic_username: str | None = field(default=None, repr=False)
    basic_password: str | None = field(default=None, repr=False)
    merchant_id: str | None = None
    request_customer_id_field: str = "customer_id"
    request_merchant_id_field: str = "tenant_id"
    tenant_id_field: str = "tenant_id"
    customer_id_field: str = "customer_id"
    data_path: str = "data"
    success_field: str | None = None
    success_value: str = "0"
    segment_field: str = "segment"
    service_tags_field: str = "service_tags"
    tier_field: str = "tier"
    points_balance_field: str = "points_balance"

    def __post_init__(self) -> None:
        BusinessHTTPConfiguration.safe_origin(self.base_url)
        BusinessHTTPConfiguration.safe_path(self.profile_path)
        BusinessHTTPConfiguration.safe_path(self.membership_path)
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.auth_mode in {"bearer", "api_key"} and not self.auth_token:
            raise ValueError("auth_token is required")
        if self.auth_mode == "basic" and (
            not self.basic_username or not self.basic_password
        ):
            raise ValueError("basic credentials are required")
        if self.auth_mode == "api_key":
            if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", self.api_key_header):
                raise ValueError("api_key_header is invalid")
            BusinessHTTPConfiguration.safe_auth_header(self.api_key_header)
        if not self.tenant_id_field or not self.customer_id_field:
            raise ValueError("ownership field mapping is required")
        for name in ("request_customer_id_field", "request_merchant_id_field"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", getattr(self, name)):
                raise ValueError(f"{name} is not a safe request parameter")
        if self.request_customer_id_field == self.request_merchant_id_field:
            raise ValueError("customer and merchant request fields must differ")
        if self.merchant_id is not None and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}", self.merchant_id
        ):
            raise ValueError("merchant_id is invalid")
        for name in (
            "tenant_id_field",
            "customer_id_field",
            "data_path",
            "segment_field",
            "service_tags_field",
            "tier_field",
            "points_balance_field",
        ):
            value = getattr(self, name)
            if not value or any(
                part in {"", "__proto__", "prototype", "constructor"}
                for part in value.split(".")
            ):
                raise ValueError(f"{name} has an unsafe field mapping")
        if self.success_field is not None and not self.success_field.strip():
            raise ValueError("success_field cannot be blank")

    @classmethod
    def from_business_connection(
        cls,
        configuration: BusinessHTTPConfiguration,
        credentials: BusinessConnectionCredentials | None = None,
    ) -> "CustomerHTTPConfig":
        if not configuration.customer_profile_path or not configuration.membership_path:
            raise ValueError("customer query paths are not configured")
        return cls(
            base_url=configuration.base_url,
            profile_path=configuration.customer_profile_path,
            membership_path=configuration.membership_path,
            timeout_seconds=configuration.timeout_seconds,
            auth_mode=configuration.auth_mode,
            auth_token=credentials.token.get_secret_value()
            if credentials and credentials.token
            else None,
            api_key_header=configuration.api_key_header,
            basic_username=credentials.username.get_secret_value()
            if credentials and credentials.username
            else None,
            basic_password=credentials.password.get_secret_value()
            if credentials and credentials.password
            else None,
            merchant_id=configuration.merchant_id,
            request_customer_id_field=configuration.request_customer_id_field,
            request_merchant_id_field=configuration.request_merchant_id_field,
            tenant_id_field=configuration.tenant_id_field,
            customer_id_field=configuration.customer_id_field,
            data_path=configuration.data_path,
            success_field=configuration.success_field,
            success_value=configuration.success_value,
            segment_field=configuration.customer_segment_field,
            service_tags_field=configuration.customer_tags_field,
            tier_field=configuration.membership_tier_field,
            points_balance_field=configuration.membership_points_field,
        )


class CustomerHTTPProvider:
    """POST-only adapter with fixed paths, pinned target and no write methods."""

    def __init__(
        self,
        config: CustomerHTTPConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or PinnedBusinessTransport(
            config.base_url, [config.profile_path, config.membership_path]
        )

    async def query_profile(
        self, context: BusinessQueryContext
    ) -> OwnedCustomerProfile:
        data = await self._fetch(self._config.profile_path, context)
        ownership = self._ownership(data, context)
        raw_tags = self._value(data, self._config.service_tags_field)
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list) or any(
            not isinstance(tag, str) for tag in raw_tags
        ):
            raise ValueError("upstream service tags are invalid")
        segment = self._value(data, self._config.segment_field)
        if segment is not None and not isinstance(segment, str):
            raise ValueError("upstream segment is invalid")
        return OwnedCustomerProfile(
            ownership=ownership,
            result=CustomerProfileResult(segment=segment, service_tags=raw_tags),
        )

    async def query_membership(self, context: BusinessQueryContext) -> OwnedMembership:
        data = await self._fetch(self._config.membership_path, context)
        ownership = self._ownership(data, context)
        return OwnedMembership(
            ownership=ownership,
            result=MembershipResult(
                tier=self._required_text(data, self._config.tier_field),
                points_balance=self._required_int(
                    data, self._config.points_balance_field
                ),
            ),
        )

    async def _fetch(
        self, path: str, context: BusinessQueryContext
    ) -> dict[str, object]:
        if not context.external_customer_id:
            raise BusinessProviderAccessDenied("customer identity is not bound")
        headers = {"Accept": "application/json"}
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
            auth=auth,
        ) as client:
            response = await safe_business_response(
                client.post(
                    f"{self._config.base_url.rstrip('/')}{path}",
                    headers=headers,
                    json={
                        self._config.request_merchant_id_field: self._config.merchant_id
                        or str(context.tenant_id),
                        self._config.request_customer_id_field: context.external_customer_id,
                    },
                )
            )
        if not 200 <= response.status_code < 300:
            raise RuntimeError("customer provider returned HTTP error")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("upstream payload must be an object")
        if self._config.success_field is not None:
            outcome = self._value(payload, self._config.success_field)
            if (
                not isinstance(outcome, (str, int, float, bool))
                or str(outcome) != self._config.success_value
            ):
                raise ValueError("upstream business outcome was not successful")
        data = self._value(payload, self._config.data_path)
        if not isinstance(data, dict):
            raise ValueError("upstream data must be an object")
        return data

    def _ownership(
        self, data: dict[str, object], context: BusinessQueryContext
    ) -> CustomerOwnership:
        tenant = self._required_text(data, self._config.tenant_id_field)
        customer = self._required_text(data, self._config.customer_id_field)
        if (
            tenant != (self._config.merchant_id or str(context.tenant_id))
            or customer != context.external_customer_id
        ):
            raise BusinessProviderAccessDenied("customer ownership mismatch")
        return CustomerOwnership(
            tenant_id=context.tenant_id,
            visitor_id=context.visitor_id,
            external_customer_id=customer,
        )

    @staticmethod
    def _value(data: dict[str, object], path: str) -> object | None:
        current: object = data
        for part in path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    @classmethod
    def _required_text(cls, data: dict[str, object], path: str) -> str:
        value = cls._value(data, path)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("upstream required text is missing")
        return value.strip()

    @classmethod
    def _required_int(cls, data: dict[str, object], path: str) -> int:
        value = cls._value(data, path)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("upstream integer is invalid")
        return value


OwnedResult = OwnedCustomerProfile | OwnedMembership


class CustomerQueryService:
    """Fail closed on identity, ownership, timeout, provider or audit errors."""

    def __init__(
        self,
        provider: ReadOnlyCustomerProvider,
        audit_sink: BusinessQueryAuditSink,
        *,
        timeout_seconds: float,
        audit_fingerprint_key: bytes,
    ) -> None:
        if timeout_seconds <= 0 or len(audit_fingerprint_key) < 32:
            raise ValueError("positive timeout and 32-byte audit key are required")
        self._provider = provider
        self._audit_sink = audit_sink
        self._timeout_seconds = timeout_seconds
        self._audit_key = bytes(audit_fingerprint_key)

    async def query_profile(
        self, context: BusinessQueryContext
    ) -> CustomerProfileResult:
        owned = await self._execute(
            "customer_profile_query",
            context,
            lambda: self._provider.query_profile(context),
        )
        return cast(OwnedCustomerProfile, owned).result

    async def query_membership(self, context: BusinessQueryContext) -> MembershipResult:
        owned = await self._execute(
            "membership_query",
            context,
            lambda: self._provider.query_membership(context),
        )
        return cast(OwnedMembership, owned).result

    async def _execute(
        self,
        operation: Literal["customer_profile_query", "membership_query"],
        context: BusinessQueryContext,
        call: Callable[[], Awaitable[OwnedResult]],
    ) -> OwnedResult:
        start = monotonic()
        if not context.external_customer_id:
            await self._audit(operation, "denied", context, start)
            raise BusinessQueryAccessDenied("无法确认客户身份，请转人工核验")
        try:
            result = await asyncio.wait_for(call(), timeout=self._timeout_seconds)
        except BusinessProviderAccessDenied:
            await self._audit(operation, "denied", context, start)
            raise BusinessQueryAccessDenied("无法确认客户归属，请转人工核验") from None
        except TimeoutError:
            await self._audit(operation, "timeout", context, start)
            raise BusinessQueryTimeout("业务查询超时，请稍后重试或转人工") from None
        except Exception:
            await self._audit(operation, "failed", context, start)
            raise BusinessQueryProviderError("业务查询暂时不可用") from None
        owner = result.ownership
        if (
            owner.tenant_id != context.tenant_id
            or owner.visitor_id != context.visitor_id
            or owner.external_customer_id != context.external_customer_id
        ):
            await self._audit(operation, "denied", context, start)
            raise BusinessQueryAccessDenied("无法确认客户归属，请转人工核验")
        await self._audit(operation, "success", context, start)
        return result

    async def _audit(
        self,
        operation: BusinessQueryOperation,
        outcome: BusinessQueryOutcome,
        context: BusinessQueryContext,
        start: float,
    ) -> None:
        def fingerprint(value: str) -> str:
            return hmac.new(
                self._audit_key,
                f"{context.tenant_id.hex}\0{value}".encode(),
                hashlib.sha256,
            ).hexdigest()

        try:
            await self._audit_sink.record(
                BusinessQueryAuditEvent(
                    tenant_id=context.tenant_id,
                    conversation_id=context.conversation_id,
                    request_id=context.request_id,
                    actor_id=context.actor_id,
                    operation=operation,
                    outcome=outcome,
                    visitor_fingerprint=fingerprint(context.visitor_id),
                    parameter_fingerprint=fingerprint(
                        context.external_customer_id or ""
                    ),
                    source_message_digest=(
                        message_lookup_digest(context.tenant_id, context.source_message_id)
                        if context.source_message_id is not None else None
                    ),
                    response_message_digest=(
                        message_lookup_digest(context.tenant_id, context.response_client_msg_no)
                        if context.response_client_msg_no is not None else None
                    ),
                    duration_ms=(monotonic() - start) * 1000,
                )
            )
        except Exception:
            raise BusinessQueryAuditError("业务查询审计失败，请转人工处理") from None

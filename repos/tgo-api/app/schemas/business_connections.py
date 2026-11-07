"""Public connection configuration never includes saved credentials."""

from datetime import datetime
from ipaddress import ip_address
import re
from typing import Literal, Self
from urllib.parse import unquote, urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_serializer,
    field_validator,
    model_validator,
)

BusinessCapability = Literal[
    "order_query",
    "logistics_query",
    "customer_profile_query",
    "membership_query",
]
ConnectionMode = Literal["http", "demo"]


def _default_capabilities() -> list[BusinessCapability]:
    return ["order_query", "logistics_query"]


class BusinessMerchantMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success_field: str | None = Field(default=None, max_length=200)
    success_value: str = Field(default="0", max_length=100)
    merchant_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]*$",
    )
    request_order_no_field: str = "order_no"
    request_customer_id_field: str = "customer_id"
    request_merchant_id_field: str = "tenant_id"
    request_operation_field: str | None = "operation"
    order_operation_value: str = Field(
        default="order_query", min_length=1, max_length=100
    )
    logistics_operation_value: str = Field(
        default="logistics_query", min_length=1, max_length=100
    )
    order_status_map: dict[str, str] = Field(
        default_factory=dict, max_length=50
    )
    logistics_status_map: dict[str, str] = Field(
        default_factory=dict, max_length=50
    )
    not_found_values: list[str] = Field(default_factory=list, max_length=50)
    logistics_not_shipped_values: list[str] = Field(
        default_factory=list, max_length=50
    )
    default_timezone: str = Field(default="UTC", min_length=1, max_length=100)

    @field_validator(
        "request_order_no_field",
        "request_customer_id_field",
        "request_merchant_id_field",
        "request_operation_field",
    )
    @classmethod
    def request_parameter(cls, value: str | None) -> str | None:
        if value is not None and (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,99}", value)
            or value in {"__proto__", "prototype", "constructor"}
        ):
            raise ValueError("请求参数名称必须明确且不能使用保留名称")
        return value

    @field_validator("order_status_map", "logistics_status_map")
    @classmethod
    def status_dictionary(cls, value: dict[str, str]) -> dict[str, str]:
        for key, label in value.items():
            if any(
                not text
                or text != text.strip()
                or len(text) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in text)
                for text in (key, label)
            ):
                raise ValueError("状态编码和业务名称必须为 128 字以内的单行文字")
        return value

    @field_validator("not_found_values", "logistics_not_shipped_values")
    @classmethod
    def outcome_codes(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value) or any(
            not text
            or text != text.strip()
            or len(text) > 128
            or any(ord(char) < 32 or ord(char) == 127 for char in text)
            for text in value
        ):
            raise ValueError("业务结果编码不能重复或包含空白及控制字符")
        return value

    @field_validator("default_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        if value == "UTC":
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("请选择有效的业务系统时区") from None
        return value

    @model_validator(mode="after")
    def coherent_mapping(self) -> Self:
        names = [
            self.request_order_no_field,
            self.request_customer_id_field,
            self.request_merchant_id_field,
        ]
        if self.request_operation_field is not None:
            names.append(self.request_operation_field)
        if len(names) != len(set(names)):
            raise ValueError("订单、客户、商户及操作参数不能使用相同名称")
        if self.not_found_values and (
            not self.success_field
            or self.success_value in self.not_found_values
        ):
            raise ValueError("未查到订单编码必须对应独立的业务结果字段且不能表示成功")
        if set(self.logistics_status_map) & set(
            self.logistics_not_shipped_values
        ):
            raise ValueError("未发货编码不能同时配置为已发货物流状态")
        return self


class BusinessHTTPConfiguration(BusinessMerchantMapping):
    base_url: str = Field(min_length=1, max_length=1000)
    order_path: str = Field(default="/v1/orders/query", max_length=300)
    logistics_path: str = Field(default="/v1/logistics/query", max_length=300)
    customer_profile_path: str | None = Field(default=None, max_length=300)
    membership_path: str | None = Field(default=None, max_length=300)
    method: Literal["GET", "POST"] = "POST"
    timeout_seconds: float = Field(default=5, ge=0.5, le=30, strict=True)
    auth_mode: Literal["none", "bearer", "api_key", "basic"] = "none"
    api_key_header: str = Field(
        default="X-API-Key", pattern=r"^[A-Za-z0-9-]{1,100}$"
    )
    data_path: str = Field(default="data", max_length=200)
    tenant_id_field: str = Field(default="tenant_id", max_length=200)
    customer_id_field: str = Field(default="customer_id", max_length=200)
    order_no_field: str = Field(default="order_no", max_length=200)
    order_status_field: str = Field(default="status", max_length=200)
    order_amount_field: str = Field(default="amount_minor", max_length=200)
    order_amount_unit: Literal["minor", "major"] = "minor"
    order_currency_field: str = Field(default="currency", max_length=200)
    order_created_at_field: str = Field(default="created_at", max_length=200)
    logistics_status_field: str = Field(default="status", max_length=200)
    logistics_carrier_field: str = Field(default="carrier", max_length=200)
    logistics_tracking_no_field: str = Field(
        default="tracking_no", max_length=200
    )
    logistics_updated_at_field: str = Field(
        default="updated_at", max_length=200
    )
    customer_segment_field: str = Field(default="segment", max_length=200)
    customer_tags_field: str = Field(default="service_tags", max_length=200)
    membership_tier_field: str = Field(default="tier", max_length=200)
    membership_points_field: str = Field(
        default="points_balance", max_length=200
    )

    @field_validator("customer_profile_path", "membership_path")
    @classmethod
    def safe_customer_path(cls, value: str | None) -> str | None:
        return cls.safe_path(value) if value is not None else None

    @field_validator(
        "customer_segment_field",
        "customer_tags_field",
        "membership_tier_field",
        "membership_points_field",
    )
    @classmethod
    def safe_customer_field(cls, value: str) -> str:
        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", value
        ):
            raise ValueError("会员与客户资料字段只支持明确的点号路径")
        return value

    @model_validator(mode="after")
    def complete_customer_paths(self) -> Self:
        paths = (self.customer_profile_path, self.membership_path)
        if (paths[0] is None) != (paths[1] is None):
            raise ValueError("客户资料与会员查询路径必须同时配置")
        if (
            paths[0] is not None
            and len(set((self.order_path, self.logistics_path, *paths))) != 4
        ):
            raise ValueError("各业务查询路径不能复用")
        return self

    @field_validator("api_key_header")
    @classmethod
    def safe_auth_header(cls, value: str) -> str:
        if value.lower() in {
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
            "proxy-authorization",
            "accept-encoding",
            "content-type",
            "x-tgo-tenant-id",
            "x-tgo-visitor-id",
            "x-tgo-request-id",
        }:
            raise ValueError("此请求头不能作为业务鉴权凭证")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_origin(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if (
                parsed.scheme != "https"
                or not host
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
                or parsed.port not in {None, 443}
                or any(char.isspace() for char in value)
                or "\\" in value
                or "%" in value
            ):
                raise ValueError
            try:
                if not ip_address(host).is_global:
                    raise ValueError
            except ValueError:
                if (
                    not re.fullmatch(
                        r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host
                    )
                    or "." not in host
                    or host.endswith((".local", ".localhost"))
                ):
                    raise ValueError
                # Literal non-global IPv4 addresses must not fall through as
                # DNS names.
                if re.fullmatch(r"[0-9.]+", host):
                    raise ValueError
        except ValueError:
            raise ValueError("请使用不含凭证或参数的公网 HTTPS 地址及标准端口") from None
        return value.rstrip("/")

    @field_validator("order_path", "logistics_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        decoded = unquote(unquote(value))
        if (
            not value.startswith("/")
            or decoded.startswith("//")
            or any(part in {".", ".."} for part in decoded.split("/"))
            or any(char in decoded for char in "?#\\")
            or any(char.isspace() or ord(char) < 32 for char in decoded)
        ):
            raise ValueError("查询路径必须保留在指定业务系统内，不能包含凭证或查询参数")
        return value


class BusinessConnectionCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr | None = Field(
        default=None, min_length=1, max_length=8192
    )
    username: SecretStr | None = Field(
        default=None, min_length=1, max_length=500
    )
    password: SecretStr | None = Field(
        default=None, min_length=1, max_length=8192
    )

    @field_validator("token", "username", "password")
    @classmethod
    def no_controls(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(
            ord(char) < 32 or ord(char) == 127
            for char in value.get_secret_value()
        ):
            raise ValueError("凭证不能包含控制字符")
        return value


class BusinessConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    name: str = Field(min_length=1, max_length=100)
    mode: ConnectionMode = "http"
    capabilities: list[BusinessCapability] = Field(
        default_factory=_default_capabilities,
        min_length=1,
        max_length=4,
    )
    configuration: BusinessHTTPConfiguration | None = None
    credentials: BusinessConnectionCredentials | None = None
    enabled: Literal[False] = False

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请填写连接名称")
        return value.strip()

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("业务能力不能重复")
        if self.mode == "demo":
            if any(
                capability in {"customer_profile_query", "membership_query"}
                for capability in self.capabilities
            ):
                raise ValueError("示例连接不支持客户资料或会员查询")
            if self.configuration is not None or self.credentials is not None:
                raise ValueError("示例连接不接受真实地址或凭证")
            return self
        if self.configuration is None:
            raise ValueError("请填写业务系统连接配置")
        if any(
            capability in {"customer_profile_query", "membership_query"}
            for capability in self.capabilities
        ):
            if (
                not self.configuration.customer_profile_path
                or not self.configuration.membership_path
            ):
                raise ValueError("客户资料和会员查询都需要配置固定路径")
            if self.configuration.method != "POST":
                raise ValueError("客户资料和会员查询必须使用只读 POST 接口")
        credentials = self.credentials
        auth = self.configuration.auth_mode
        if auth == "none" and credentials is not None:
            raise ValueError("无需鉴权的连接不能保存额外凭证")
        if auth in {"bearer", "api_key"} and (
            credentials is None
            or credentials.token is None
            or credentials.username
            or credentials.password
        ):
            raise ValueError("请填写此鉴权方式需要的 Token")
        if auth == "basic" and (
            credentials is None
            or credentials.username is None
            or credentials.password is None
            or credentials.token
        ):
            raise ValueError("请填写此鉴权方式需要的账号和密码")
        return self


class BusinessConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    mode: ConnectionMode | None = None
    capabilities: list[BusinessCapability] | None = Field(
        default=None, min_length=1, max_length=4
    )
    configuration: BusinessHTTPConfiguration | None = None
    credentials: BusinessConnectionCredentials | None = None
    enabled: bool | None = Field(default=None, strict=True)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("请填写连接名称")
        return value.strip() if value is not None else None


class BusinessConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    project_id: UUID
    name: str
    mode: ConnectionMode
    capabilities: list[BusinessCapability]
    configuration: BusinessHTTPConfiguration | None
    credentials_configured: bool
    enabled: bool
    revision: int
    configuration_revision: int
    authorization_revision: int
    resource_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    test_status: Literal["not_tested", "passed", "unavailable"]
    created_at: datetime
    updated_at: datetime


class BusinessConnectionResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    actor_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    purpose: Literal["configuration_test"]


class BusinessConnectionRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


class BusinessConnectionPrivateSnapshot(BaseModel):
    (
        "Only returned on the separately authenticated private resolution rout"
        "e."
    )

    model_config = ConfigDict(extra="forbid")
    scope: Literal[
        "business_connection_configuration"
    ] = "business_connection_configuration"
    can_enable: Literal[False] = False
    connection: BusinessConnectionResponse
    credentials: BusinessConnectionCredentials | None

    @field_serializer("credentials")
    def private_credentials(
        self, value: BusinessConnectionCredentials | None
    ) -> dict[str, str] | None:
        if value is None:
            return None
        return {
            name: secret.get_secret_value()
            for name, secret in (
                ("token", value.token),
                ("username", value.username),
                ("password", value.password),
            )
            if secret is not None
        }

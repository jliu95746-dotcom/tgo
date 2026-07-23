"""Internal read-only order and logistics query endpoints."""

from __future__ import annotations

import hashlib
import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.config import settings
from app.domain.business_tools.audit_sink import DatabaseBusinessQueryAuditSink
from app.domain.business_tools.demo_provider import (
    DemoHandbagBusinessProvider,
    LoggingBusinessQueryAuditSink,
)
from app.domain.business_tools.http_provider import (
    HTTPBusinessProvider,
    HTTPBusinessProviderConfig,
)
from app.domain.business_tools.models import (
    BusinessQueryProviderStatus,
    BusinessQueryRequest,
    BusinessQueryResponse,
    LogisticsQueryInput,
    OrderQueryInput,
)
from app.domain.business_tools.providers import ReadOnlyBusinessProvider
from app.domain.business_tools.service import (
    BusinessQueryAccessDenied,
    BusinessQueryAuditError,
    BusinessQueryError,
    BusinessQueryProviderError,
    BusinessQueryService,
    BusinessQueryTimeout,
)


router = APIRouter(prefix="/business", tags=["read-only-business"])
InternalAPIKey = Annotated[str | None, Header(alias="X-Internal-API-Key")]


def _require_internal_key(provided_key: str | None) -> None:
    configured_key = settings.INTERNAL_API_KEY or settings.SECRET_KEY
    if not configured_key or not provided_key or not hmac.compare_digest(
        configured_key,
        provided_key,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API key",
        )


def _secret_value(secret: object | None) -> str | None:
    if secret is None:
        return None
    getter = getattr(secret, "get_secret_value", None)
    return str(getter()) if callable(getter) else str(secret)


def _build_http_provider_config() -> HTTPBusinessProviderConfig:
    if not settings.BUSINESS_API_BASE_URL:
        raise ValueError("BUSINESS_API_BASE_URL is required in http mode")
    return HTTPBusinessProviderConfig(
        base_url=settings.BUSINESS_API_BASE_URL,
        order_path=settings.BUSINESS_API_ORDER_PATH,
        logistics_path=settings.BUSINESS_API_LOGISTICS_PATH,
        method=settings.BUSINESS_API_METHOD,
        timeout_seconds=settings.BUSINESS_QUERY_TIMEOUT,
        auth_mode=settings.BUSINESS_API_AUTH_MODE,
        auth_token=_secret_value(settings.BUSINESS_API_AUTH_TOKEN),
        api_key_header=settings.BUSINESS_API_KEY_HEADER,
        basic_username=settings.BUSINESS_API_BASIC_USERNAME,
        basic_password=_secret_value(settings.BUSINESS_API_BASIC_PASSWORD),
        data_path=settings.BUSINESS_API_DATA_PATH,
        success_field=settings.BUSINESS_API_SUCCESS_FIELD,
        success_value=settings.BUSINESS_API_SUCCESS_VALUE,
        tenant_id_field=settings.BUSINESS_API_TENANT_ID_FIELD,
        customer_id_field=settings.BUSINESS_API_CUSTOMER_ID_FIELD,
        order_no_field=settings.BUSINESS_API_ORDER_NO_FIELD,
        order_status_field=settings.BUSINESS_API_ORDER_STATUS_FIELD,
        order_amount_field=settings.BUSINESS_API_ORDER_AMOUNT_FIELD,
        order_amount_unit=settings.BUSINESS_API_ORDER_AMOUNT_UNIT,
        order_currency_field=settings.BUSINESS_API_ORDER_CURRENCY_FIELD,
        order_created_at_field=settings.BUSINESS_API_ORDER_CREATED_AT_FIELD,
        logistics_status_field=settings.BUSINESS_API_LOGISTICS_STATUS_FIELD,
        logistics_carrier_field=settings.BUSINESS_API_LOGISTICS_CARRIER_FIELD,
        logistics_tracking_no_field=(
            settings.BUSINESS_API_LOGISTICS_TRACKING_NO_FIELD
        ),
        logistics_updated_at_field=(
            settings.BUSINESS_API_LOGISTICS_UPDATED_AT_FIELD
        ),
    )


def _build_business_service() -> BusinessQueryService:
    audit_key = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    provider: ReadOnlyBusinessProvider
    if settings.BUSINESS_QUERY_MODE == "demo":
        provider = DemoHandbagBusinessProvider()
        audit_sink = LoggingBusinessQueryAuditSink()
    elif settings.BUSINESS_QUERY_MODE == "http":
        provider = HTTPBusinessProvider(_build_http_provider_config())
        audit_sink = DatabaseBusinessQueryAuditSink()
    else:
        raise ValueError("Business query provider is not configured")
    return BusinessQueryService(
        provider,
        audit_sink,
        timeout_seconds=settings.BUSINESS_QUERY_TIMEOUT,
        audit_fingerprint_key=audit_key,
    )


@router.get("/status", response_model=BusinessQueryProviderStatus)
async def get_business_query_status(
    x_internal_api_key: InternalAPIKey = None,
) -> BusinessQueryProviderStatus:
    """Report provider readiness without returning URLs or credentials."""
    _require_internal_key(x_internal_api_key)
    mode = settings.BUSINESS_QUERY_MODE
    configured = mode == "demo"
    if mode == "http":
        try:
            _build_http_provider_config()
            configured = True
        except ValueError:
            configured = False
    return BusinessQueryProviderStatus(
        mode=mode,
        configured=configured,
        auth_mode=settings.BUSINESS_API_AUTH_MODE if mode == "http" else None,
        method=settings.BUSINESS_API_METHOD if mode == "http" else None,
        order_path=settings.BUSINESS_API_ORDER_PATH if mode == "http" else None,
        logistics_path=(
            settings.BUSINESS_API_LOGISTICS_PATH if mode == "http" else None
        ),
        timeout_seconds=settings.BUSINESS_QUERY_TIMEOUT,
    )


@router.post("/query", response_model=BusinessQueryResponse)
async def query_business_data(
    request: BusinessQueryRequest,
    x_internal_api_key: InternalAPIKey = None,
) -> BusinessQueryResponse:
    """Run a tenant-bound read-only query against the selected provider."""
    _require_internal_key(x_internal_api_key)
    try:
        service = _build_business_service()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    try:
        if request.operation == "order_query":
            result = await service.query_order(
                OrderQueryInput(order_no=request.order_no),
                request.context.to_domain(),
            )
            return BusinessQueryResponse(operation=request.operation, order=result)
        result = await service.query_logistics(
            LogisticsQueryInput(order_no=request.order_no),
            request.context.to_domain(),
        )
        return BusinessQueryResponse(
            operation=request.operation,
            logistics=result,
        )
    except BusinessQueryAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except BusinessQueryTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except BusinessQueryProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    except BusinessQueryAuditError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except BusinessQueryError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

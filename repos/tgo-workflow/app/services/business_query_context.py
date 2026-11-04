"""Bind private customer headers to the verified employee execution origin."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID

from pydantic import JsonValue, SecretStr, ValidationError

from app.schemas.business_query import BusinessChannel, BusinessExecutionContext
from app.schemas.execution import WorkflowExecutionOrigin


def require_business_query_context(
    project_id: str,
    origin: WorkflowExecutionOrigin | None,
    *, context_token: str | None,
    visitor_id: str | None,
    request_fingerprint: str | None,
    channel: str | None,
) -> BusinessExecutionContext:
    if (
        origin is None or origin.business_project_id is None
        or origin.business_request_id is None or origin.visitor_session_id is None
        or not context_token or not visitor_id or not request_fingerprint or not channel
    ):
        raise PermissionError("受控业务查询缺少已验证的客户会话")
    try:
        result = BusinessExecutionContext(
            project_id=origin.business_project_id,
            request_id=origin.business_request_id,
            visitor_session_id=origin.visitor_session_id,
            visitor_id=UUID(visitor_id),
            request_fingerprint=request_fingerprint,
            channel=cast(BusinessChannel, channel),
            context_token=SecretStr(context_token),
        )
    except (ValueError, ValidationError):
        raise PermissionError("受控业务查询客户会话格式无效") from None
    if str(result.project_id) != project_id:
        raise PermissionError("受控业务查询客户会话企业范围不一致")
    return result


def business_query_record_inputs(
    inputs: Mapping[str, object],
) -> dict[str, JsonValue]:
    """The real order number lives only in the synchronous call stack."""
    if set(inputs) != {"order_no"}:
        raise ValueError("受控业务查询只接受订单号输入")
    return {"order_no": "[REDACTED]"}

"""Fresh private owner reads; no client-supplied customer identifiers are accepted."""

from datetime import UTC, datetime
import hashlib

import httpx

from app.config import settings
from app.exceptions import AuthorizationError, ExternalServiceError
from app.schemas.business_execution_context import BusinessContextCheck, BusinessRequestPrincipal


async def verify_context(client: httpx.AsyncClient, internal_url: str,
                         check: BusinessContextCheck) -> BusinessRequestPrincipal:
    try:
        response = await client.post(internal_url.rstrip("/") + "/ai/business-execution/context-check",
            json={**check.model_dump(mode="json"), "context_token": check.context_token.get_secret_value()},
            headers={"X-Internal-API-Key": settings.secret_key}, timeout=3, follow_redirects=False)
    except httpx.RequestError:
        raise ExternalServiceError("tgo-api", "当前客户会话暂不可核验") from None
    if response.status_code in {401, 403, 404, 409}:
        raise AuthorizationError("当前客户会话或原业务请求已失效")
    if response.status_code != 200 or len(response.content) > 65536:
        raise ExternalServiceError("tgo-api", "当前客户会话核验结果无效")
    try:
        result = BusinessRequestPrincipal.model_validate_json(response.content)
        if (result.model_dump(include=set(BusinessContextCheck.model_fields) - {"context_token"}) != check.model_dump()
            or result.context_digest != hashlib.sha256(check.context_token.get_secret_value().encode()).hexdigest()
            or not set(BusinessRequestPrincipal.model_fields).issubset(result.model_fields_set)
            or not -30 <= (datetime.now(UTC) - result.checked_at).total_seconds() <= 60):
            raise ValueError("Business context identity mismatch")
    except ValueError:
        raise ExternalServiceError("tgo-api", "当前客户会话核验结果无效") from None
    return result

"""Bounded private read of Plugin's tenant-scoped query audit events."""

from uuid import UUID

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.core.exceptions import TGOAPIException
from app.schemas.business_query_audit import BusinessQueryAuditList


def audit_unavailable() -> TGOAPIException:
    return TGOAPIException(
        "业务查询审计暂不可读取，请稍后重试",
        code="BUSINESS_QUERY_AUDIT_UNAVAILABLE",
        status_code=503,
    )


async def read_business_query_audits(
    project_id: UUID, query_id: UUID,
) -> BusinessQueryAuditList:
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is None or len(token.get_secret_value()) < 32:
        raise audit_unavailable()
    try:
        async with httpx.AsyncClient(
            timeout=10, trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream(
                "GET",
                settings.PLUGIN_RUNTIME_URL.rstrip("/")
                + f"/business/execution/audits/{query_id}",
                params={"project_id": str(project_id)},
                headers={"X-Business-Service-Token": token.get_secret_value()},
            ) as response:
                if response.status_code != 200:
                    raise audit_unavailable()
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > 65536:
                        raise audit_unavailable()
                    chunks.append(chunk)
        result = BusinessQueryAuditList.model_validate_json(b"".join(chunks))
        if result.total < len(result.events) or any(
            event.project_id != project_id or event.query_id != query_id
            for event in result.events
        ):
            raise audit_unavailable()
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise audit_unavailable() from None


async def read_business_message_audits(
    project_id: UUID,
    source_message_id: str,
    response_client_msg_no: str,
    skip: int,
    limit: int,
) -> BusinessQueryAuditList:
    """Find Plugin attempts using a message identity read from API-owned records."""
    token = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if token is None or len(token.get_secret_value()) < 32:
        raise audit_unavailable()
    try:
        async with httpx.AsyncClient(
            timeout=10, trust_env=False, follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                settings.PLUGIN_RUNTIME_URL.rstrip("/")
                + "/business/execution/audits/by-message",
                json={
                    "project_id": str(project_id),
                    "source_message_id": source_message_id,
                    "response_client_msg_no": response_client_msg_no,
                    "skip": skip,
                    "limit": limit,
                },
                headers={"X-Business-Service-Token": token.get_secret_value()},
            ) as response:
                if response.status_code != 200:
                    raise audit_unavailable()
                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > 65536:
                        raise audit_unavailable()
                    chunks.append(chunk)
        result = BusinessQueryAuditList.model_validate_json(b"".join(chunks))
        if result.total < len(result.events) or len(result.events) > limit or any(
            event.project_id != project_id for event in result.events
        ):
            raise audit_unavailable()
        return result
    except (httpx.HTTPError, ValidationError, ValueError):
        raise audit_unavailable() from None

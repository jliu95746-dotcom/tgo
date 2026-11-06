"""Keep customer authorization out of model input, messages and public serialization."""

from datetime import UTC, datetime
from typing import TypedDict
from uuid import UUID

from app.core.database import SessionLocal
from app.schemas.business_execution_context import (
    BusinessContextCheck,
    BusinessRequestAuthorization,
    business_request_fingerprint,
)
from app.schemas.knowledge import KnowledgeChannel
from app.services.business_execution_context import (
    BusinessExecutionContextService,
)


class BusinessAuthorizationOptions(TypedDict, total=False):
    business_authorization: BusinessRequestAuthorization


def authorization_options(
    value: BusinessRequestAuthorization | None,
) -> BusinessAuthorizationOptions:
    return {} if value is None else {"business_authorization": value}


def business_headers(
    value: BusinessRequestAuthorization | None,
    project: str,
    visitor: str | None,
    channel: str | None,
) -> dict[str, str]:
    if value is None:
        return {}
    if (
        value.project_id != UUID(project)
        or visitor is None
        or value.visitor_id != UUID(visitor)
        or value.channel != KnowledgeChannel(channel or "internal")
        or value.expires_at.tzinfo is None
        or value.expires_at <= datetime.now(UTC)
        or not value.context_token.get_secret_value()
    ):
        raise ValueError("当前客户会话范围不一致或已失效")
    return {
        "X-Business-Request-Context": value.context_token.get_secret_value(),
        "X-Business-Request-ID": str(value.request_id),
    }


def verify_business_publication(
    value: BusinessRequestAuthorization | None,
    project: str,
    visitor: str,
    message: str,
    channel: str | None,
) -> None:
    if value is None:
        return
    business_headers(value, project, visitor, channel)
    check = BusinessContextCheck(
        project_id=UUID(project),
        visitor_id=UUID(visitor),
        channel=KnowledgeChannel(channel or "internal"),
        request_id=value.request_id,
        request_fingerprint=business_request_fingerprint(
            UUID(visitor), message
        ),
        context_token=value.context_token,
    )
    # Publication uses a new owner transaction, never the earlier route Session.
    with SessionLocal() as db:
        principal = BusinessExecutionContextService(db).verify(check)
    if principal.context_digest != value.context_digest:
        raise ValueError("原业务请求依据发生变化，答复暂停发送")

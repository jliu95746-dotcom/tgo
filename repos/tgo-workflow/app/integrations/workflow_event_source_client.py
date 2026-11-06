"""Verify the current API-owned authority of an automatic business event."""

import httpx

from app.config import settings
from app.integrations.http_client import HttpClient
from app.schemas.event_trigger import WorkflowEventSourceVerification


async def verify_event_source(query: WorkflowEventSourceVerification) -> None:
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise RuntimeError("工作流业务事件内部核验未配置。")
    try:
        client = await HttpClient.get_client()
        response = await client.post(
            settings.API_INTERNAL_SERVICE_URL.rstrip("/")
            + "/internal/workflows/events/source/verify",
            json=query.model_dump(mode="json"),
            headers={"X-Internal-API-Key": key.get_secret_value()},
            timeout=5.0,
        )
    except (httpx.HTTPError, OSError, TimeoutError):
        raise RuntimeError("工作流业务事件来源暂时无法核验。") from None
    if response.status_code == 409:
        raise PermissionError("工作流业务事件订阅或来源已失效。")
    if response.status_code != 200:
        raise RuntimeError("工作流业务事件来源暂时无法核验。")
    try:
        receipt = WorkflowEventSourceVerification.model_validate(
            response.json()
        )
    except ValueError:
        raise RuntimeError("工作流业务事件来源核验回执无效。") from None
    if receipt != query:
        raise PermissionError("工作流业务事件来源回执与当前任务不一致。")

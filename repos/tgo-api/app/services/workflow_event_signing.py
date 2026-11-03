"""Sign exactly the serialized bytes forwarded by the workflow client."""

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from pydantic import JsonValue

from app.schemas.workflow_events import WorkflowEventTestRequest
from app.schemas.workflow_event_subscriptions import WorkflowSourceEventRequest


@dataclass(frozen=True)
class SignedWorkflowEvent:
    body: bytes
    headers: dict[str, str]


def sign_event_test(
    workflow_id: str,
    project_id: str,
    actor_staff_id: str,
    request: WorkflowEventTestRequest,
    secret: str,
) -> SignedWorkflowEvent:
    if not secret:
        raise HTTPException(503, "工作流内部事件核验未配置。")
    # Each actor owns a retry namespace; this ID never contains customer input.
    scope = json.dumps(
        [
            project_id,
            actor_staff_id,
            workflow_id,
            str(request.request_id),
        ]
    )
    event_id = "admin-test-" + str(
        uuid5(
            NAMESPACE_URL,
            f"tgo-event-test:v1:{scope}",
        )
    )
    payload: dict[str, JsonValue] = {
        "project_id": project_id,
        "event_type": request.event_type,
        "expected_release_id": request.expected_release_id,
        "expected_release_digest": request.expected_release_digest,
        "data": request.data,
    }
    return _sign_payload(workflow_id, event_id, payload, secret)


def sign_source_event(
    workflow_id: str,
    project_id: str,
    delivery_id: UUID,
    request: WorkflowSourceEventRequest,
    secret: str,
) -> SignedWorkflowEvent:
    if request.source_binding.delivery_id != delivery_id:
        raise ValueError("Source event delivery binding mismatch")
    payload: dict[str, JsonValue] = {
        "project_id": project_id,
        "event_type": request.event_type,
        "expected_release_id": request.expected_release_id,
        "expected_release_digest": request.expected_release_digest,
        "source_binding": request.source_binding.model_dump(mode="json"),
        "data": request.data,
    }
    return _sign_payload(
        workflow_id, "source-" + str(delivery_id), payload, secret
    )


def _sign_payload(
    workflow_id: str,
    event_id: str,
    payload: dict[str, JsonValue],
    secret: str,
) -> SignedWorkflowEvent:
    if not secret:
        raise HTTPException(503, "工作流内部事件核验未配置。")
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (UnicodeError, ValueError):
        raise HTTPException(422, "事件数据必须是有效的 UTF-8 JSON。") from None
    if len(body) > 65536:
        raise HTTPException(413, "事件数据过大，请缩短测试内容。")
    timestamp = str(int(time.time()))
    prefix = timestamp + "\n" + event_id + "\n" + workflow_id + "\n"
    signed = prefix.encode() + body
    signature = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return SignedWorkflowEvent(
        body,
        {
            "X-Workflow-Timestamp": timestamp,
            "X-Workflow-Event-ID": event_id,
            "X-Workflow-Signature": "sha256=" + signature,
        },
    )

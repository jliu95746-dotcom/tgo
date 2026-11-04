"""Current-state checks against the API service's staff authority."""

from uuid import UUID
from collections.abc import Mapping

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.integrations.http_client import HttpClient


class ApprovalAssigneeReceipt(BaseModel):
    project_id: UUID
    assignee_ids: list[UUID] = Field(min_length=1, max_length=100)


async def verify_approval_assignees(project_id: str, assignee_ids: set[str]) -> None:
    """Reject a revoked account or an unverified response before action."""
    if not assignee_ids:
        return
    key = settings.SECRET_KEY
    if key is None or not key.get_secret_value():
        raise RuntimeError("工作流审批人内部核验未配置")
    try:
        project_uuid = UUID(project_id)
        staff_uuids = {UUID(value) for value in assignee_ids}
    except ValueError:
        raise PermissionError("工作流审批人或企业编号无效") from None
    if len(staff_uuids) != len(assignee_ids) or len(staff_uuids) > 100:
        raise PermissionError("工作流审批人列表无效")
    try:
        client = await HttpClient.get_client()
        response = await client.post(
            settings.API_INTERNAL_SERVICE_URL.rstrip("/")
            + "/internal/workflows/approvals/assignees/verify",
            json={
                "project_id": str(project_uuid),
                "assignee_ids": sorted(str(value) for value in staff_uuids),
            },
            headers={"X-Internal-API-Key": key.get_secret_value()},
            timeout=5.0,
        )
    except (httpx.HTTPError, OSError, TimeoutError):
        raise RuntimeError("工作流审批人归属暂时无法核验") from None
    if response.status_code == 409:
        raise PermissionError("工作流审批人已撤权或不属于当前企业")
    if response.status_code != 200:
        raise RuntimeError("工作流审批人归属暂时无法核验")
    try:
        receipt = ApprovalAssigneeReceipt.model_validate(response.json())
    except ValueError:
        raise RuntimeError("工作流审批人核验回执无效") from None
    if receipt.project_id != project_uuid or set(receipt.assignee_ids) != staff_uuids:
        raise PermissionError("工作流审批人归属回执与当前流程不一致")


async def verify_definition_approval_assignees(
    definition: Mapping[str, object], project_id: str,
) -> None:
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        return
    ids: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") != "approval":
            continue
        data = node.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("approver_staff_id"), str):
            raise PermissionError("工作流审批人配置无效")
        ids.add(data["approver_staff_id"])
    await verify_approval_assignees(project_id, ids)

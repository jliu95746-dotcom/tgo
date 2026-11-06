"""Typed calls to AI-owned durable business reports. No raw upstream errors."""

from uuid import UUID

from fastapi import HTTPException

from app.schemas.agent_business_trials import (
    BusinessTrialRequest,
    BusinessTrialReport,
    BusinessTrialCheck,
    BusinessTrialAuthority,
)
from app.services.agent_activation_policy_client import PolicyTransport, decode
from app.schemas.agent_business_requests import (
    BusinessRequestControl,
    BusinessRequestState,
    BusinessEmployeeCurrentState,
)
from app.schemas.agent_business_reviews import (
    BusinessReviewRequest,
    BusinessReviewRecord,
    BusinessReviewState,
    AgentBusinessCapabilities,
)
from app.schemas.agent_business_review_requests import (
    BusinessReviewRequestState,
)


async def current_employee(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID
) -> BusinessEmployeeCurrentState:
    from datetime import UTC, datetime

    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/business-current-state",
        extra_headers=client._trial_headers(project, actor),
        timeout=15,
    )
    result = BusinessEmployeeCurrentState.model_validate(
        decode(response, BusinessEmployeeCurrentState)
    )
    if (
        len(response.content) > 16_384
        or result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
        or result.model_fields_set
        != set(BusinessEmployeeCurrentState.model_fields)
        or not -30
        <= (datetime.now(UTC) - result.checked_at).total_seconds()
        <= 60
    ):
        raise HTTPException(502, "当前员工状态范围不一致或已失效")
    return result


def record_scope(
    record: BusinessReviewRecord,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report_id: UUID,
) -> bool:
    return (
        record.project_id == project
        and record.reviewed_by == actor
        and record.agent_id == agent
        and record.report_id == report_id
        and record.model_fields_set == set(BusinessReviewRecord.model_fields)
        and record.checks.model_fields_set
        == set(type(record.checks).model_fields)
    )


async def review(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report_id: UUID,
    *,
    command: BusinessReviewRequest | None = None,
    request_id: UUID | None = None,
) -> BusinessReviewRecord:
    endpoint = f"/api/v1/agents/{agent}/business-trials/{report_id}/reviews"
    if request_id is not None:
        endpoint += f"/requests/{request_id}"
    response = await client._make_request(
        "POST" if command else "GET",
        endpoint,
        json_data=command.model_dump(mode="json") if command else None,
        extra_headers=client._trial_headers(project, actor),
        timeout=30,
    )
    result = BusinessReviewRecord.model_validate(
        decode(response, BusinessReviewRecord)
    )
    if (
        len(response.content) > 65_536
        or not record_scope(result, project, actor, agent, report_id)
        or request_id is not None
        and result.request_id != request_id
        or command is not None
        and (
            result.request_id != command.request_id
            or result.decision != command.decision
            or result.checks != command.checks
            or result.reason != command.reason
        )
    ):
        raise HTTPException(502, "业务评审结果与原请求不一致")
    return result


async def review_state(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report_id: UUID,
) -> BusinessReviewState:
    from datetime import UTC, datetime

    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/business-trials/{report_id}/reviews",
        extra_headers=client._trial_headers(project, actor),
        timeout=30,
    )
    result = BusinessReviewState.model_validate(
        decode(response, BusinessReviewState)
    )
    if (
        len(response.content) > 256 * 1024
        or result.project_id != project
        or result.agent_id != agent
        or result.report_id != report_id
        or result.checked_by != actor
        or result.model_fields_set != set(BusinessReviewState.model_fields)
        or not -30
        <= (datetime.now(UTC) - result.checked_at).total_seconds()
        <= 60
        or any(
            not record_scope(row, project, actor, agent, report_id)
            for row in result.reviews
        )
    ):
        raise HTTPException(502, "业务评审状态范围不一致或已失效")
    return result


async def review_request_state(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report_id: UUID,
    request_id: UUID,
    *,
    cancel: bool = False,
) -> BusinessReviewRequestState:
    from datetime import UTC, datetime

    response = await client._make_request(
        "POST" if cancel else "GET",
        f"/api/v1/agents/{agent}/business-trials/{report_id}/reviews/requests/{request_id}/"
        + ("cancel" if cancel else "state"),
        extra_headers=client._trial_headers(project, actor),
        timeout=20,
    )
    result = BusinessReviewRequestState.model_validate(
        decode(response, BusinessReviewRequestState)
    )
    if (
        len(response.content) > 128 * 1024
        or result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
        or result.report_id != report_id
        or result.request_id != request_id
        or result.model_fields_set
        != set(BusinessReviewRequestState.model_fields)
        or not -30
        <= (datetime.now(UTC) - result.checked_at).total_seconds()
        <= 60
        or result.result is not None
        and not record_scope(result.result, project, actor, agent, report_id)
        or result.command is not None
        and (
            result.command.model_fields_set
            != set(BusinessReviewRequest.model_fields)
            or result.command.checks.model_fields_set
            != set(type(result.command.checks).model_fields)
        )
        or cancel
        and result.status == "pending"
    ):
        raise HTTPException(502, "原评审请求状态范围不一致或已失效")
    return result


async def resume_review(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report_id: UUID,
    request_id: UUID,
) -> BusinessReviewRecord:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{agent}/business-trials/{report_id}/reviews/requests/{request_id}/resume",
        extra_headers=client._trial_headers(project, actor),
        timeout=35,
    )
    result = BusinessReviewRecord.model_validate(
        decode(response, BusinessReviewRecord)
    )
    if (
        len(response.content) > 65_536
        or not record_scope(result, project, actor, agent, report_id)
        or result.request_id != request_id
    ):
        raise HTTPException(502, "原评审续办回执范围不一致")
    return result


async def capabilities(
    client: PolicyTransport, project: UUID, actor: UUID, agent: UUID
) -> AgentBusinessCapabilities:
    from datetime import UTC, datetime

    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{agent}/business-capabilities",
        extra_headers=client._trial_headers(project, actor),
        timeout=30,
    )
    result = AgentBusinessCapabilities.model_validate(
        decode(response, AgentBusinessCapabilities)
    )
    if (
        len(response.content) > 256 * 1024
        or result.project_id != project
        or result.agent_id != agent
        or result.checked_by != actor
        or result.model_fields_set
        != set(AgentBusinessCapabilities.model_fields)
        or not -30
        <= (datetime.now(UTC) - result.checked_at).total_seconds()
        <= 60
        or any(
            row.model_fields_set != set(type(row).model_fields)
            for row in result.capabilities
        )
    ):
        raise HTTPException(502, "业务能力状态范围不一致或已失效")
    return result


async def report(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    *,
    command: BusinessTrialRequest | None = None,
    request_id: UUID | None = None,
    report_id: UUID | None = None,
    cancel: bool = False,
) -> BusinessTrialReport:
    endpoint = f"/api/v1/agents/{agent}/business-trials"
    if request_id is not None:
        endpoint += f"/requests/{request_id}"
    elif report_id is not None:
        endpoint += f"/{report_id}" + ("/cancel" if cancel else "")
    response = await client._make_request(
        "POST" if command or cancel else "GET",
        endpoint,
        json_data=command.private_payload() if command else None,
        extra_headers=client._trial_headers(project, actor),
        timeout=20,
    )
    result = BusinessTrialReport.model_validate(
        decode(response, BusinessTrialReport)
    )
    if (
        len(response.content) > 65_536
        or result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
        or result.model_fields_set != set(BusinessTrialReport.model_fields)
        or (request_id is not None and result.request_id != request_id)
        or (report_id is not None and result.id != report_id)
        or (
            command is not None
            and any(
                getattr(result, key) != getattr(command, key)
                for key in ("connection_id", "request_id", "operation")
            )
        )
    ):
        raise HTTPException(502, "业务测试报告与原请求不一致")
    return result


async def request_state(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request_id: UUID,
    *,
    cancel: BusinessRequestControl | None = None,
) -> BusinessRequestState:
    if cancel is not None and cancel.request_id != request_id:
        raise HTTPException(409, "取消命令与原请求编号不一致")
    response = await client._make_request(
        "POST" if cancel else "GET",
        f"/api/v1/agents/{agent}/business-trials/requests/{request_id}/"
        + ("cancel" if cancel else "state"),
        json_data=cancel.model_dump(mode="json") if cancel else None,
        extra_headers=client._trial_headers(project, actor),
        timeout=20,
    )
    result = BusinessRequestState.model_validate(
        decode(response, BusinessRequestState)
    )
    if (
        len(response.content) > 128 * 1024
        or result.project_id != project
        or result.actor_id != actor
        or result.agent_id != agent
        or result.request_id != request_id
        or result.model_fields_set != set(BusinessRequestState.model_fields)
        or result.report is not None
        and result.report.model_fields_set
        != set(BusinessTrialReport.model_fields)
        or cancel is not None
        and (
            result.connection_id != cancel.connection_id
            or result.operation != cancel.operation
            or result.status == "preparing"
        )
    ):
        raise HTTPException(502, "业务请求状态与原请求不一致")
    return result


async def authorize(
    client: PolicyTransport, check: BusinessTrialCheck
) -> BusinessTrialAuthority:
    command = check.command
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{command.agent_id}/business-trials/authorize",
        json_data=check.private_payload(),
        extra_headers=client._trial_headers(
            command.project_id, command.actor_id
        ),
        timeout=20,
    )
    result = BusinessTrialAuthority.model_validate(
        decode(response, BusinessTrialAuthority)
    )
    from datetime import UTC, datetime

    if (
        len(response.content) > 65_536
        or not result.matches(command)
        or result.phase != check.phase
        or result.model_fields_set != set(BusinessTrialAuthority.model_fields)
        or not -5
        <= (datetime.now(UTC) - result.checked_at).total_seconds()
        <= 30
        or result.expires_at <= datetime.now(UTC)
    ):
        raise HTTPException(502, "业务测试授权与原请求不一致或已失效")
    return result

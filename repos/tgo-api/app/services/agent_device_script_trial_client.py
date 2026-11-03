"""Fixed-binding trials through the existing trusted AI transport."""

from uuid import UUID
from typing import TypeVar

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.schemas.agent_device_script_trials import (
    ScriptTrialReport,
    ScriptTrialStart,
)
from app.services.agent_activation_policy_client import PolicyTransport
from app.schemas.agent_device_script_requests import (
    ScriptTrialControl,
    ScriptTrialRequestState,
)
from app.schemas.agent_device_script_cases import (
    ScriptCasePage,
    ScriptCaseSaveControl,
    ScriptCaseSaveState,
    ScriptCaseRead,
    ScriptCaseRecord,
    ScriptCaseWrite,
    ScriptCaseTrialResult,
    ScriptTrialEvidence,
)


def _report(
    response: httpx.Response,
    project: UUID,
    actor: UUID,
    employee: UUID,
    *,
    request_id: UUID | None = None,
    report_id: UUID | None = None,
    command: ScriptTrialStart | None = None,
) -> ScriptTrialReport:
    if not response.is_success:
        raise HTTPException(
            response.status_code,
            "脚本试验尚未确认，请按原请求编号查询，不要重新运行",
        )
    try:
        value = ScriptTrialReport.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "脚本试验服务返回了无效结果") from None
    if (
        not set(ScriptTrialReport.model_fields).issubset(
            value.model_fields_set
        )
        or value.project_id != project
        or value.actor_id != actor
        or value.agent_id != employee
        or request_id is not None
        and value.request_id != request_id
        or report_id is not None
        and value.id != report_id
        or command is not None
        and (
            value.binding_id != command.binding_id
            or value.binding_revision != command.binding_revision
        )
    ):
        raise HTTPException(502, "脚本试验结果与原请求范围不一致")
    return value


async def start(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    command: ScriptTrialStart,
) -> ScriptTrialReport:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{employee}/device-script-trials",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
        timeout=180,
    )
    return _report(
        response,
        project,
        actor,
        employee,
        request_id=command.request_id,
        command=command,
    )


async def read(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    report_id: UUID,
) -> ScriptTrialReport:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{employee}/device-script-trials/{report_id}",
        extra_headers=client._trial_headers(project, actor),
        timeout=45,
    )
    return _report(response, project, actor, employee, report_id=report_id)


async def recover(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    request_id: UUID,
) -> ScriptTrialReport:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{employee}/device-script-trials/requests/"
        f"{request_id}",
        extra_headers=client._trial_headers(project, actor),
        timeout=45,
    )
    return _report(response, project, actor, employee, request_id=request_id)


def _request_state(
    response: httpx.Response,
    project: UUID,
    actor: UUID,
    employee: UUID,
    request: UUID,
    command: ScriptTrialControl | None = None,
) -> ScriptTrialRequestState:
    if not response.is_success:
        raise HTTPException(response.status_code, "原请求状态未确认，请按原编号继续查询")
    try:
        value = ScriptTrialRequestState.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "脚本请求状态返回了无效结果") from None
    if (
        not set(ScriptTrialRequestState.model_fields).issubset(
            value.model_fields_set
        )
        or value.project_id != project
        or value.actor_id != actor
        or value.agent_id != employee
        or value.request_id != request
        or command is not None
        and (
            value.binding_id != command.binding_id
            or value.binding_revision != command.binding_revision
        )
        or value.report is not None
        and not set(ScriptTrialReport.model_fields).issubset(
            value.report.model_fields_set
        )
    ):
        raise HTTPException(502, "原请求状态与登录身份或请求范围不一致")
    return value


async def request_state(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    request: UUID,
) -> ScriptTrialRequestState:
    response = await client._make_request(
        "GET",
        f"/api/v1/agents/{employee}/device-script-trials/requests/"
        f"{request}/state",
        extra_headers=client._trial_headers(project, actor),
        timeout=45,
    )
    return _request_state(response, project, actor, employee, request)


async def cancel_request(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    employee: UUID,
    command: ScriptTrialControl,
) -> ScriptTrialRequestState:
    response = await client._make_request(
        "POST",
        f"/api/v1/agents/{employee}/device-script-trials/requests/"
        f"{command.request_id}/cancel",
        json_data=command.model_dump(mode="json"),
        extra_headers=client._trial_headers(project, actor),
        timeout=45,
    )
    return _request_state(
        response, project, actor, employee, command.request_id, command
    )


CaseReply = TypeVar("CaseReply", bound=BaseModel)


def _complete_fields(value: object) -> bool:
    if isinstance(value, BaseModel):
        names = set(type(value).model_fields)
        return names.issubset(value.model_fields_set) and all(
            _complete_fields(getattr(value, name)) for name in names
        )
    if isinstance(value, list):
        return all(_complete_fields(item) for item in value)
    return True


async def _case_call(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    method: str,
    path: str,
    schema: type[CaseReply],
    *,
    command: BaseModel | None = None,
    params: dict[str, object] | None = None,
    timeout: float = 45,
) -> CaseReply:
    headers = client._trial_headers(project, actor)
    if command is None:
        response = await client._make_request(
            method, path, extra_headers=headers, params=params, timeout=timeout
        )
    else:
        response = await client._make_request(
            method,
            path,
            extra_headers=headers,
            timeout=timeout,
            json_data=command.model_dump(mode="json"),
        )
    if not response.is_success:
        raise HTTPException(response.status_code, "用例或结果未确认，请查询原编号")
    try:
        result = schema.model_validate_json(response.content)
    except ValidationError:
        raise HTTPException(502, "用例或结果服务返回无效内容") from None
    if not _complete_fields(result):
        raise HTTPException(502, "用例或结果缺少明确的范围信息")
    return result


def _owned_case(
    value: ScriptCaseRecord,
    project: UUID,
    agent: UUID,
    *,
    version: UUID | None = None,
) -> None:
    if (
        value.project_id != project
        or value.agent_id != agent
        or (version is not None and value.id != version)
    ):
        raise HTTPException(502, "用例版本不属于当前员工和企业")


async def list_cases(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    *,
    skip: int = 0,
    limit: int = 20,
) -> ScriptCasePage:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        f"/api/v1/agents/{agent}/device-script-cases",
        ScriptCasePage,
        params={"skip": skip, "limit": limit},
    )
    if (
        value.project_id != project
        or value.agent_id != agent
        or value.skip != skip
        or value.limit != limit
        or len(value.items) > limit
        or value.total < len(value.items)
        or len({item.case_id for item in value.items}) != len(value.items)
    ):
        raise HTTPException(502, "用例目录与当前查询范围不一致")
    for item in value.items:
        _owned_case(item, project, agent)
    return value


async def save_case(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    command: ScriptCaseWrite,
) -> ScriptCaseRecord:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "POST",
        f"/api/v1/agents/{agent}/device-script-cases",
        ScriptCaseRecord,
        command=command,
    )
    _owned_case(value, project, agent)
    if (
        value.created_by != actor
        or value.request_id != command.request_id
        or value.binding_id != command.binding_id
        or value.binding_revision != command.binding_revision
        or value.revision != command.expected_revision + 1
        or command.case_id is not None
        and value.case_id != command.case_id
    ):
        raise HTTPException(502, "用例保存结果与原请求不一致")
    return value


async def read_case(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    version: UUID,
) -> ScriptCaseRead:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        f"/api/v1/agents/{agent}/device-script-cases/versions/{version}",
        ScriptCaseRead,
    )
    _owned_case(value.case, project, agent, version=version)
    return value


async def recover_case(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
) -> ScriptCaseRecord:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        f"/api/v1/agents/{agent}/device-script-cases/requests/{request}",
        ScriptCaseRecord,
    )
    _owned_case(value, project, agent)
    if value.created_by != actor or value.request_id != request:
        raise HTTPException(502, "用例原请求与当前操作者不一致")
    return value


async def case_save_state(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    request: UUID,
    *,
    command: ScriptCaseSaveControl | None = None,
) -> ScriptCaseSaveState:
    suffix = "state" if command is None else "cancel"
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET" if command is None else "POST",
        f"/api/v1/agents/{agent}/device-script-cases/requests/"
        f"{request}/{suffix}",
        ScriptCaseSaveState,
        command=command,
    )
    if (
        value.project_id != project
        or value.actor_id != actor
        or value.agent_id != agent
        or value.request_id != request
    ):
        raise HTTPException(502, "用例保存状态不属于当前原请求")
    if command is not None and any(
        getattr(value, name) != getattr(command, name)
        for name in ScriptCaseSaveControl.model_fields
    ):
        raise HTTPException(502, "用例保存取消范围与原请求不一致")
    return value


async def run_case(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    version: UUID,
    command: ScriptTrialControl,
) -> ScriptCaseTrialResult:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "POST",
        f"/api/v1/agents/{agent}/device-script-cases/versions/"
        f"{version}/trials",
        ScriptCaseTrialResult,
        command=command,
        timeout=180,
    )
    report = value.report
    if (
        value.case_version_id != version
        or report.project_id != project
        or report.actor_id != actor
        or report.agent_id != agent
        or report.request_id != command.request_id
        or report.binding_id != command.binding_id
        or report.binding_revision != command.binding_revision
    ):
        raise HTTPException(502, "用例执行结果与原版本或请求不一致")
    return value


async def read_evidence(
    client: PolicyTransport,
    project: UUID,
    actor: UUID,
    agent: UUID,
    report: UUID,
) -> ScriptTrialEvidence:
    value = await _case_call(
        client,
        project,
        actor,
        agent,
        "GET",
        f"/api/v1/agents/{agent}/device-script-trials/{report}/evidence",
        ScriptTrialEvidence,
    )
    if (
        value.project_id != project
        or value.actor_id != actor
        or value.agent_id != agent
        or value.report_id != report
    ):
        raise HTTPException(502, "结果证据与当前操作者或报告不一致")
    if value.case is not None:
        _owned_case(value.case, project, agent)
    return value

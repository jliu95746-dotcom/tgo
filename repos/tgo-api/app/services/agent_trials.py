"""Administrator trial orchestration with shared authorization and durable receipts."""

import asyncio
from uuid import UUID

from anyio import CancelScope
from fastapi import HTTPException

from app.schemas.agent_trials import AgentTrialReport, AgentTrialRequest
from app.services import agent_trial_usage as usage
from app.services.ai_client import ai_client
from app.services.ai_usage_runtime import bind_permit


async def _finish(project_id: UUID, staff_id: UUID, report: AgentTrialReport) -> AgentTrialReport:
    await asyncio.to_thread(usage.finish_trial, project_id, staff_id, report)
    return report


async def lookup(project_id: UUID, staff_id: UUID, agent_id: UUID, request: AgentTrialRequest) -> AgentTrialReport:
    return await _finish(project_id, staff_id, await ai_client.match_agent_trial_request(
        project_id, staff_id, agent_id, request,
    ))


async def start(project_id: UUID, staff_id: UUID, agent_id: UUID, request: AgentTrialRequest) -> AgentTrialReport:
    try:
        return await lookup(project_id, staff_id, agent_id, request)
    except HTTPException as error:
        if error.status_code != 404:
            raise
    permit = await asyncio.to_thread(usage.reserve_trial, project_id, staff_id, agent_id, request)
    with bind_permit(permit):
        try:
            report = await ai_client.start_agent_trial(project_id, staff_id, agent_id, request)
            return await _finish(project_id, staff_id, report)
        except (Exception, asyncio.CancelledError) as error:
            # A transport failure is not proof that generation never happened.
            if permit is not None:
                with CancelScope(shield=True):
                    if isinstance(error, HTTPException) and error.status_code in {400, 401, 403, 404, 409, 422}:
                        await asyncio.to_thread(usage.release_trial, permit)
                    else:
                        await asyncio.to_thread(usage.defer_trial, permit)
            raise


async def by_request(project_id: UUID, staff_id: UUID, agent_id: UUID, request_key: UUID) -> AgentTrialReport:
    return await _finish(project_id, staff_id, await ai_client.agent_trial_by_request(project_id, staff_id, agent_id, request_key))


async def get(project_id: UUID, staff_id: UUID, agent_id: UUID, report_id: UUID) -> AgentTrialReport:
    return await _finish(project_id, staff_id, await ai_client.get_agent_trial(project_id, staff_id, agent_id, report_id))


async def cancel(project_id: UUID, staff_id: UUID, agent_id: UUID, report_id: UUID) -> AgentTrialReport:
    return await _finish(project_id, staff_id, await ai_client.cancel_agent_trial(project_id, staff_id, agent_id, report_id))


async def list_reports(project_id: UUID, staff_id: UUID, agent_id: UUID, limit: int) -> list[AgentTrialReport]:
    reports = await ai_client.list_agent_trials(project_id, staff_id, agent_id, limit)
    return [await _finish(project_id, staff_id, report) for report in reports]

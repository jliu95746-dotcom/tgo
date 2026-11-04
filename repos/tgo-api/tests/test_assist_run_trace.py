"""Capture each model call used to produce a staff-visible assist draft."""

from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from app.services.ai_client import AIServiceClient
from app.services.assist_run_trace import capture_assist_run_traces


@pytest.mark.asyncio
async def test_trace_collects_only_calls_inside_draft_scope() -> None:
    client = AIServiceClient()
    project_id = str(uuid4())
    agent_id = str(uuid4())
    system_prompt = "本次 API 系统指令，包含不应出现在回执中的内容"
    template = "固定业务事实生成模板"
    response = httpx.Response(
        200,
        json={
            "success": True,
            "content": "可以咨询。",
            "metadata": {
                "model_name": "openai:gpt-4o",
                "configured_prompt_sha256": "a" * 64,
            },
        },
        request=httpx.Request("POST", "http://test/agents/run"),
    )
    client._make_request = AsyncMock(return_value=response)
    with capture_assist_run_traces() as traces:
        await client.run_supervisor_agent(
            message="问候", project_id=project_id, agent_id=agent_id,
            system_message=system_prompt, trace_phase="fact_answer",
            trace_template=template,
        )
        await client.run_supervisor_agent(
            message="改写",
            project_id=project_id,
            trace_phase="expression_rewrite",
        )
        await client.run_supervisor_agent(
            message="审核", project_id=project_id, trace_phase="fact_audit"
        )
    await client.run_supervisor_agent(
        message="不属于草稿", project_id=project_id, trace_phase="fact_audit"
    )
    assert [trace.phase for trace in traces] == [
        "fact_answer",
        "expression_rewrite",
        "fact_audit",
    ]
    assert all(trace.model_name == "openai:gpt-4o" for trace in traces)
    assert all(trace.configured_prompt_sha256 == "a" * 64 for trace in traces)
    assert str(traces[0].requested_agent_id) == agent_id
    assert traces[0].request_system_prompt_sha256 == sha256(
        system_prompt.encode("utf-8")
    ).hexdigest()
    assert traces[0].api_template_sha256 == sha256(
        template.encode("utf-8")
    ).hexdigest()
    assert traces[1].api_template_sha256 is None
    assert traces[1].request_system_prompt_sha256 is None
    assert system_prompt not in traces[0].model_dump_json()
    assert template not in traces[0].model_dump_json()
    assert client._make_request.await_args_list[0].kwargs[
        "json_data"
    ]["system_message"] == system_prompt


@pytest.mark.asyncio
async def test_trace_never_trusts_malformed_metadata() -> None:
    client = AIServiceClient()
    project_id = str(uuid4())
    response = httpx.Response(
        200,
        json={
            "content": "回复",
            "metadata": {
                "model_name": "x" * 500,
                "configured_prompt_sha256": "not-a-hash",
            },
        },
        request=httpx.Request("POST", "http://test/agents/run"),
    )
    client._make_request = AsyncMock(return_value=response)
    with capture_assist_run_traces() as traces:
        await client.run_supervisor_agent(
            message="问候", project_id=project_id, trace_phase="fact_answer"
        )
    assert len(traces) == 1
    assert traces[0].model_name is None
    assert traces[0].configured_prompt_sha256 is None

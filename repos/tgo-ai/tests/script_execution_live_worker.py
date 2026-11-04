"""Opt-in child process for the owned Control/Go loopback integration test."""

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import UUID


async def main() -> None:
    if os.getenv("TGO_SCRIPT_AI_LOOPBACK_TEST") != "owned-runtime-only":
        raise RuntimeError("Explicit isolated runtime opt-in is required")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.config import settings
    from app.schemas.device_script_execution import ScriptPolicyRead
    from app.schemas.device_script_policy import ScriptDescriptor
    from app.services.device_execution import track_device_execution
    from app.services.device_control_client import device_control_client
    from app.services.device_script_execution import ApprovedScriptExecution

    fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    endpoint = urlsplit(fixture["url"])
    if (
        endpoint.scheme != "http"
        or endpoint.hostname != "127.0.0.1"
        or endpoint.username is not None
        or endpoint.path
        or endpoint.query
        or endpoint.fragment
    ):
        raise RuntimeError("Only the owned loopback test listener is allowed")
    settings.secret_key = fixture["test_secret"]
    settings.device_control_service_url = fixture["url"]
    bound = ScriptPolicyRead.model_validate(fixture["policy"])
    descriptor = ScriptDescriptor.model_validate(fixture["descriptor"])
    (
        current,
        reviewed,
    ) = await device_control_client.validate_script_configuration(
        bound.project_id,
        bound.device_id,
    )
    assert current.policy.digest == bound.policy.digest
    assert reviewed.model_dump() == descriptor.model_dump()
    bound, descriptor = current, reviewed
    before = await device_control_client.read_script_budget(
        bound.project_id,
        bound.device_id,
        bound.policy.digest,
        descriptor.grant_digest,
    )
    assert before.remaining_runs == 1 and not before.can_enable
    assert before.server_policy_used == before.device_policy_used == 0
    context = SimpleNamespace(
        project_id=str(bound.project_id),
        agent=SimpleNamespace(
            id=UUID(fixture["agent_id"]),
            name="隔离 AI 调用验收",
            bound_device_id=str(bound.device_id),
        ),
        execution_id=UUID(fixture["session_id"]),
        parent_workflow_execution_id=UUID(fixture["workflow_execution_id"]),
        disable_tools=False,
        response_purpose="standard",
    )
    async with track_device_execution(context) as execution:
        run = ApprovedScriptExecution.prepare(
            bound,
            descriptor,
            {"label": fixture["label"]},
            execution_id=UUID(fixture["execution_id"]),
        )
        first = await run.run()
        again = await run.run()
        assert first.status == again.status == "succeeded"
        assert first.result_available and again.fields == first.fields
        assert first.fields == {"ready": True, "label": fixture["label"]}
        assert (
            first.workflow_execution_id == context.parent_workflow_execution_id
        )
        after = await device_control_client.read_script_budget(
            bound.project_id,
            bound.device_id,
            bound.policy.digest,
            descriptor.grant_digest,
        )
        assert after.remaining_runs == 0 and not after.can_enable
        assert after.server_policy_used == after.device_policy_used == 1
        assert after.server_grant_used == after.device_grant_used == 1
        assert after.connection_instance_id == before.connection_instance_id
        assert after.session_id == before.session_id
        execution.complete(True)
    print(
        json.dumps({"same_receipt": True, "session_closed": execution.closed})
    )


if __name__ == "__main__":
    asyncio.run(main())

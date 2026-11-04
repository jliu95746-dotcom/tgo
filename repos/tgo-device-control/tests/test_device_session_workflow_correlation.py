"""A parent workflow run is immutable metadata on one device session."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.device_session import SessionStart
from app.services.device_sessions import DeviceSessionService


@pytest.mark.asyncio
async def test_parent_workflow_run_survives_start_and_duplicate_retry():
    project, device, session, agent, parent = (uuid4() for _ in range(5))
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(device_name="测试设备")),
        get=AsyncMock(return_value=None), add=Mock(), flush=AsyncMock(), commit=AsyncMock(),
    )
    service = DeviceSessionService(db)
    request = SessionStart(agent_id=agent, agent_name="测试员工", workflow_execution_id=parent)
    started = await service.start(project, device, session, request)
    assert started.workflow_execution_id == parent
    db.get.return_value = db.add.call_args.args[0]
    assert (await service.start(project, device, session, request)).id == session
    with pytest.raises(HTTPException) as conflict:
        await service.start(project, device, session, request.model_copy(update={
            "workflow_execution_id": uuid4(),
        }))
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_parent_filter_keeps_project_scope_in_session_query():
    project, parent = uuid4(), uuid4()
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=0),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])),
    )
    result = await DeviceSessionService(db).list(project, workflow_execution_id=parent)
    assert result.total == 0 and result.sessions == []
    query = db.execute.await_args.args[0]
    compiled = query.compile()
    assert "dc_devices.project_id" in str(compiled)
    assert "dc_sessions.workflow_execution_id" in str(compiled)
    assert project in compiled.params.values()
    assert parent in compiled.params.values()

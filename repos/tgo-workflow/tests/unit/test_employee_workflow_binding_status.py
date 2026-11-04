"""A queued employee run must stop if its workflow is deactivated."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.integrations.ai_client import AIClient
from app.services.employee_workflow_binding import (
    EmployeeWorkflowBinding,
    check_employee_binding,
)


@pytest.mark.asyncio
async def test_employee_execution_rechecks_current_workflow_switch(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    project_id = str(uuid4())
    workflow_id = str(uuid4())
    check_ai = AsyncMock()
    monkeypatch.setattr(AIClient, "verify_employee_workflow_binding", check_ai)
    binding = EmployeeWorkflowBinding(
        project_id,
        str(uuid4()),
        workflow_id,
        2,
        str(uuid4()),
        "a" * 64,
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE wf_workflows "
                    "(id VARCHAR PRIMARY KEY, project_id VARCHAR, status VARCHAR)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO wf_workflows (id, project_id, status) "
                    "VALUES (:workflow_id, :project_id, 'active')"
                ),
                {"workflow_id": workflow_id, "project_id": project_id},
            )
        async with sessions() as db:
            await check_employee_binding(binding, db)
            await db.execute(
                text("UPDATE wf_workflows SET status='inactive' WHERE id=:workflow_id"),
                {"workflow_id": workflow_id},
            )
            await db.commit()
            with pytest.raises(PermissionError, match="已停用"):
                await check_employee_binding(binding, db)
            assert check_ai.await_count == 2
    finally:
        await engine.dispose()

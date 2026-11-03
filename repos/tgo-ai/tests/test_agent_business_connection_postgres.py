"""Migration and separate concurrent requests on an explicitly isolated DB."""
import asyncio
import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Agent, BaseModel
from app.schemas.agent_business_connections import AgentBusinessConnectionWrite, BusinessConnectionOwner
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_business_connections import AgentBusinessConnectionService

URL=os.getenv("AI_POSTGRES_TEST_URL")
pytestmark=pytest.mark.skipif(not URL,reason="Explicit isolated PostgreSQL required")


@pytest.mark.asyncio
async def test_migration_concurrent_requests_and_original_receipt():
    assert URL
    url=make_url(URL)
    assert url.host in {"localhost","127.0.0.1"} and url.port==15432
    assert (url.database or "").startswith("tgo_scenarios_test_")
    schema="employee_connections_"+uuid4().hex
    admin=create_async_engine(URL)
    engine=create_async_engine(URL,connect_args={"server_settings":{"search_path":schema}})
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    path=Path(__file__).parents[1]/"migrations/versions/y7z8a9b0c1d2_agent_business_connections.py"
    spec=importlib.util.spec_from_file_location("employee_connection_migration",path)
    assert spec and spec.loader
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    def migrate(connection,method):
        with Operations.context(MigrationContext.configure(connection)):getattr(migration,method)()
    class Owner:
        async def get_agent_configuration_actor(self,q):return AgentConfigurationActor(**q.model_dump(),checked_at=datetime.now(UTC))
        async def get_agent_business_connection(self,q):
            await asyncio.sleep(0.03)
            return BusinessConnectionOwner(**q.model_dump(),name="订单",mode="http",enabled=False,
                operations=["order_query"],revision=1,configuration_revision=1,authorization_revision=1,
                resource_revision="a"*64,test_status="not_tested",checked_at=datetime.now(UTC),
                configuration_tests=[{"operation":"order_query","report_id":None,"status":"not_tested","valid_until":None,"issues":[]}])
    project,actor,agent,connection=[uuid4() for _ in range(4)]
    command=AgentBusinessConnectionWrite(request_id=uuid4(),expected_revision=0,operations=["order_query"])
    try:
        async with admin.begin() as db:await db.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as db:
            await db.run_sync(lambda sync:BaseModel.metadata.create_all(sync,tables=[
                table for name,table in BaseModel.metadata.tables.items() if name not in {
                    "ai_agent_business_connections","ai_agent_business_connection_requests"}]))
            for method in ["upgrade","downgrade","upgrade"]:await db.run_sync(migrate,method)
        async with sessions() as db:
            db.add(Agent(id=agent,project_id=project,name="迁移订单员工",model="fixture",is_active=False))
            await db.commit()
        async def save():
            async with sessions() as db:return await AgentBusinessConnectionService(db,Owner()).save(project,actor,agent,connection,command)
        first,duplicate=await asyncio.gather(save(),save())
        assert first==duplicate and first.revision==1 and not first.can_enable
        async with sessions() as db:
            service=AgentBusinessConnectionService(db,Owner())
            retired=await service.save(project,actor,agent,connection,AgentBusinessConnectionWrite(
                request_id=uuid4(),expected_revision=1,operations=["order_query"],enabled=False))
            assert retired.revision==2
        async with sessions() as db:
            service=AgentBusinessConnectionService(db,Owner())
            assert await service.request_result(project,actor,agent,command.request_id)==first
            assert not (await service.list(project,actor,agent)).bindings[0].enabled
        with pytest.raises(RuntimeError,match="records exist"):
            async with engine.begin() as db:await db.run_sync(migrate,"downgrade")
    finally:
        await engine.dispose()
        async with admin.begin() as db:await db.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()

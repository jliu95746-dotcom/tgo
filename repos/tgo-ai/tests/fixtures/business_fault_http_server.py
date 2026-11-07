"""Disposable HTTP fault server: real lifecycle, synthetic owners, no merchant."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import uvicorn

from app.api.v1 import agent_business_trials as routes
from app.api.v1.agent_trials import TrialPrincipal, require_trial_principal
from app.database import AsyncSessionLocal, engine, get_db
from app.exceptions import ConflictError, NotFoundError
from app.models import Agent, BaseModel
from app.models.agent_business_connection import AgentBusinessConnection
from app.schemas.agent_business_connections import BusinessConnectionOwner
from app.schemas.agent_business_reviews import BusinessTestEvidence
from app.schemas.agent_business_trials import BusinessTrialCheck, BusinessTrialReceipt
from app.schemas.agent_configuration_actor import AgentConfigurationActor
from app.services.agent_business_trials import AgentBusinessTrialService

directory = Path(os.environ["BUSINESS_FAULT_DIRECTORY"]).resolve()
owned_root = Path(os.environ["BUSINESS_FAULT_ROOT"]).resolve()
assert directory.name.startswith("business-fault-")
assert directory.parent == owned_root
assert engine.url.get_backend_name() == "sqlite"
assert engine.url.database is not None
assert Path(engine.url.database).resolve() == directory / "state.sqlite"
project, actor, agent, connection, proof = [
    UUID(os.environ["BUSINESS_FAULT_" + name])
    for name in ("PROJECT", "ACTOR", "AGENT", "CONNECTION", "PROOF")
]
port = int(os.environ["BUSINESS_FAULT_PORT"])


async def fingerprint(_db, _project, _actor, _agent):
    return "b" * 64


async def hold(name):
    while (directory / name).exists():
        await asyncio.sleep(0.025)


class SyntheticOwner:
    async def get_agent_configuration_actor(self, query):
        return AgentConfigurationActor(
            **query.model_dump(), checked_at=datetime.now(UTC)
        )

    async def get_agent_business_connection(self, query):
        await hold("hold-prepare")
        return BusinessConnectionOwner(
            **query.model_dump(),
            name="隔离故障样板",
            mode="http",
            enabled=True,
            operations=["order_query"],
            revision=1,
            configuration_revision=1,
            authorization_revision=1,
            resource_revision="a" * 64,
            test_status="passed",
            checked_at=datetime.now(UTC),
            configuration_tests=[
                {
                    "operation": "order_query",
                    "report_id": proof,
                    "status": "current",
                    "valid_until": datetime.fromisoformat(
                        os.environ["BUSINESS_FAULT_VALID_UNTIL"]
                    ),
                    "issues": [],
                }
            ],
        )

    async def get_agent_business_test_evidence(self, query):
        await hold("hold-review")
        return BusinessTestEvidence(
            **query.model_dump(),
            status="current",
            issues=[],
            checked_at=datetime.now(UTC),
        )

    async def query_agent_business_trial(self, command):
        async with AsyncSessionLocal() as db:
            await service(db).authorize(
                BusinessTrialCheck(command=command, phase="before_query")
            )
        count = directory / "query-count"
        count.write_text(
            str(int(count.read_text() if count.exists() else "0") + 1), encoding="utf-8"
        )
        return BusinessTrialReceipt(
            **command.identity(),
            outcome="passed",
            summary={"status": "已发货"},
            error_code=None,
            customer_evidence={
                "binding_id": uuid4(),
                "binding_revision": 1,
                "policy_revision": 1,
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
            },
            checked_at=datetime.now(UTC),
        )


owner = SyntheticOwner()


def service(db):
    return AgentBusinessTrialService(db, owner=owner, fingerprint=fingerprint)


def injected(db: AsyncSession = Depends(get_db)):
    return service(db)


async def execute(command):
    async with AsyncSessionLocal() as db:
        await service(db).run(command)


routes.execute = execute
app = FastAPI()
app.dependency_overrides[require_trial_principal] = lambda: TrialPrincipal(
    project_id=project, staff_id=actor
)
app.dependency_overrides[routes.get_service] = injected
app.include_router(routes.router, prefix="/api/v1/agents")


@app.exception_handler(ConflictError)
async def conflict(_request, _error):
    from starlette.responses import JSONResponse

    return JSONResponse(status_code=409, content={"detail": "isolated conflict"})


@app.exception_handler(NotFoundError)
async def missing(_request, _error):
    from starlette.responses import JSONResponse

    return JSONResponse(status_code=404, content={"detail": "isolated missing"})


@app.get("/health")
async def health():
    return {"status": "ok", "fixture": True}


@app.on_event("startup")
async def initialize():
    async with engine.begin() as db:
        await db.run_sync(BaseModel.metadata.create_all)
    async with AsyncSessionLocal() as db:
        if await db.scalar(select(Agent.id).where(Agent.id == agent)) is None:
            db.add(
                Agent(
                    id=agent,
                    project_id=project,
                    name="隔离故障员工",
                    model="fixture",
                    is_active=False,
                )
            )
            await db.flush()
            db.add(
                AgentBusinessConnection(
                    project_id=project,
                    agent_id=agent,
                    connection_id=connection,
                    operations=["order_query"],
                    enabled=True,
                    revision=1,
                    configuration_revision=1,
                    authorization_revision=1,
                    resource_revision="a" * 64,
                    configured_by=actor,
                )
            )
            await db.commit()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)

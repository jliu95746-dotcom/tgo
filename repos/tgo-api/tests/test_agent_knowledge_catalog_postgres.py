"""Real PostgreSQL serialization, ownership and immutable catalog history."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import Project, Staff
from app.models.agent_knowledge_catalog import AgentKnowledgeCatalog
from app.schemas.agent_activation import AgentActivationRequirementsQuery
from app.schemas.agent_knowledge_cases import KnowledgeCatalogWrite
from app.services import agent_knowledge_cases as service
from app.services.agent_activation_requirements import current_requirements
from tests.test_agent_knowledge_cases import questions


def test_catalog_scope_history_concurrency_and_activation(monkeypatch):
    address = os.getenv("KNOWLEDGE_CATALOG_TEST_URL")
    if not address:
        pytest.skip(
            "Requires opt-in local PostgreSQL with a disposable schema"
        )
    url = make_url(address).set(drivername="postgresql+psycopg2")
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    schema = "knowledge_catalog_test_" + uuid4().hex
    admin = create_engine(url)
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema}"}
    )
    factory = sessionmaker(engine)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            Base.metadata.create_all(
                connection,
                tables=[
                    Project.__table__,
                    Staff.__table__,
                    AgentKnowledgeCatalog.__table__,
                ],
            )
        project, actor, other_project, other_actor, agent = [
            uuid4() for _ in range(5)
        ]
        with factory.begin() as db:
            db.add_all(
                [
                    Project(
                        id=project,
                        name="Catalog test",
                        api_key="synthetic-one",
                    ),
                    Project(
                        id=other_project,
                        name="Other test",
                        api_key="synthetic-two",
                    ),
                ]
            )
            db.flush()
            db.add_all(
                [
                    Staff(
                        id=actor,
                        project_id=project,
                        username="catalog-one",
                        password_hash="unused",
                        role="admin",
                    ),
                    Staff(
                        id=other_actor,
                        project_id=other_project,
                        username="catalog-two",
                        password_hash="unused",
                        role="admin",
                    ),
                ]
            )
        monkeypatch.setattr(service, "SessionLocal", factory)
        first_request = KnowledgeCatalogWrite(
            request_key=uuid4(), expected_revision=0, questions=questions()
        )
        first = service._save(project, actor, agent, first_request)
        retry = service._save(project, actor, agent, first_request)
        assert first.id == retry.id and retry.current and retry.revision == 1
        query = AgentActivationRequirementsQuery(
            project_id=project,
            requested_by=actor,
            agent_id=agent,
            template_id="knowledge",
            template_version=1,
            channel="web",
        )
        with factory() as db:
            requirements = current_requirements(db, query)
        assert requirements.knowledge_cases == first.cases[:3]
        second_questions = ["企业还提供哪些支持？", *questions()[1:]]
        second = service._save(
            project,
            actor,
            agent,
            KnowledgeCatalogWrite(
                request_key=uuid4(),
                expected_revision=1,
                questions=second_questions,
            ),
        )
        assert second.revision == 2 and second.id != first.id
        assert (
            service._save(project, actor, agent, first_request).current
            is False
        )
        assert (
            service._case(project, actor, agent, first.cases[0].id)
            == first.cases[0]
        )
        with factory() as db:
            changed = current_requirements(db, query)
        assert (
            changed.requirements_revision != requirements.requirements_revision
        )
        for wrong_actor, wrong_project, wrong_agent in [
            (other_actor, project, agent),
            (other_actor, other_project, agent),
            (actor, project, uuid4()),
        ]:
            with pytest.raises(HTTPException):
                service._case(
                    wrong_project, wrong_actor, wrong_agent, first.cases[0].id
                )
        with pytest.raises(HTTPException) as conflict:
            service._save(
                project,
                actor,
                agent,
                first_request.model_copy(update={"request_key": uuid4()}),
            )
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException):
            service._save(project, actor, uuid4(), first_request)

        def competing_write():
            try:
                return service._save(
                    project,
                    actor,
                    agent,
                    KnowledgeCatalogWrite(
                        request_key=uuid4(),
                        expected_revision=2,
                        questions=questions(),
                    ),
                ).revision
            except HTTPException as error:
                return error.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: competing_write(), range(2)))
        assert sorted(results) == [3, 409]
        with factory() as db:
            rows = list(
                db.scalars(
                    select(AgentKnowledgeCatalog).order_by(
                        AgentKnowledgeCatalog.revision
                    )
                )
            )
            assert len(rows) == 3 and rows[0].questions == questions()
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(
                text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname=:name"
                    ),
                    {"name": schema},
                )
                == 0
            )
        admin.dispose()

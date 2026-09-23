"""Disposable RAG server; reuse the verified RAG-owned transaction fixture."""
import asyncio
import importlib.util
import json
import logging
import os
import socket
import sys
from pathlib import Path

import pytest
import uvicorn

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root / "repos/tgo-rag"))
from src.rag_service.database import get_db_session_dependency  # noqa: E402
from src.rag_service.main import create_app  # noqa: E402


async def main():
    logging.disable(logging.CRITICAL)
    spec = importlib.util.spec_from_file_location(
        "knowledge_fixture",
        root / "repos/tgo-rag/tests/test_knowledge_tenant_boundary.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    patch = pytest.MonkeyPatch()
    fixture = module.tenant_knowledge.__wrapped__(patch)
    server = server_task = child = None
    try:
        state = await anext(fixture)
        app = create_app()

        async def private_db():
            yield state.db

        app.dependency_overrides[get_db_session_dependency] = private_db
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(64)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app, log_level="critical", access_log=False, lifespan="off"
            )
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(100):
            if server.started:
                break
            await asyncio.sleep(0.05)
        assert server.started
        manifest = {
            "base_url": f"http://127.0.0.1:{port}",
            "tenants": [
                {
                    "project": str(t.company),
                    "faq": str(t.faq.id),
                    "site": str(t.site.id),
                    "pair": str(t.pair.id),
                    "page": str(t.page.id),
                }
                for t in state.tenants
            ],
        }
        environment = dict(os.environ)
        environment["DATABASE_URL"] = environment.pop(
            "API_KNOWLEDGE_TEST_DATABASE_URL"
        )
        child = await asyncio.create_subprocess_exec(
            str(root / "repos/tgo-api/.venv/Scripts/python.exe"),
            str(root / "scripts/native-dev/tests/knowledge-http-api.py"),
            json.dumps(manifest),
            env=environment,
        )
        assert await asyncio.wait_for(child.wait(), 180) == 0
        for queued in state.queued:
            queued.assert_not_called()
        for tenant in state.tenants:
            await state.db.refresh(tenant.pair)
            await state.db.refresh(tenant.page)
            assert tenant.pair.deleted_at is None
            assert tenant.pair.status == tenant.page.status == "pending"
        print(
            "PASS: RAG records unchanged; no processing jobs dispatched",
            flush=True,
        )
    finally:
        if child and child.returncode is None:
            child.terminate()
            await child.wait()
        if server:
            server.should_exit = True
        if server_task:
            await asyncio.wait_for(server_task, 10)
        try:
            await anext(fixture)
        except StopAsyncIteration:
            pass
        patch.undo()
    print(
        "PASS: temporary RAG server stopped; RAG fixture schema rolled back",
        flush=True,
    )


asyncio.run(main())

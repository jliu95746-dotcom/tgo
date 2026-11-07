"""A project_id parameter is never enough to access the private RAG service."""

from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from rag_service.services import service_auth


def test_private_service_auth_is_required_when_enabled(monkeypatch):
    monkeypatch.setattr(service_auth, "get_settings", lambda: SimpleNamespace(
        saas_enabled=True, saas_billing_enabled=True, saas_internal_token=SecretStr("synthetic-test-only")))
    app = FastAPI()
    app.add_middleware(service_auth.ServiceIdentityMiddleware)
    @app.get('/v1/collections')
    def collections():
        return {"ok": True}
    client = TestClient(app)
    assert client.get('/v1/collections?project_id=forged').status_code == 403
    assert client.get('/v1/collections', headers={'X-SaaS-Service-Token': 'wrong'}).status_code == 403
    assert client.get('/v1/collections', headers={'X-SaaS-Service-Token': 'synthetic-test-only'}).status_code == 200

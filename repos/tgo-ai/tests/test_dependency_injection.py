"""Test dependency injection in API endpoints."""

from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_agent_service
from app.main import app
from app.models.project import Project
from app.services.agent_service import AgentService


class TestDependencyInjection:
    """Test that services are properly injected as dependencies."""

    def test_agent_service_dependency_injection(self, client: TestClient, test_project: Project) -> None:
        """Test that AgentService is injected as a dependency."""
        mock_agent_service = Mock(spec=AgentService)
        mock_agent_service.list_agents = AsyncMock(return_value=([], 0))

        app.dependency_overrides[get_agent_service] = lambda: mock_agent_service

        try:
            response = client.get(f"/api/v1/agents?project_id={test_project.id}")

            assert response.status_code == 200
            data = response.json()
            assert data["data"] == []
            assert data["pagination"]["total"] == 0
            mock_agent_service.list_agents.assert_called_once()
        finally:
            if get_agent_service in app.dependency_overrides:
                del app.dependency_overrides[get_agent_service]

    @pytest.mark.parametrize(
        "device_id", [None, "44444444-4444-4444-8444-444444444444"]
    )
    def test_agent_service_receives_correct_parameters(
        self, client: TestClient, test_project: Project, device_id: str | None
    ) -> None:
        """Test that agent list parameters are forwarded correctly."""
        mock_agent_service = Mock(spec=AgentService)
        mock_agent_service.list_agents = AsyncMock(return_value=([], 0))

        app.dependency_overrides[get_agent_service] = lambda: mock_agent_service

        try:
            params = {
                "project_id": str(test_project.id), "is_default": "true",
                "limit": "10", "offset": "5",
            }
            if device_id is not None:
                params["bound_device_id"] = device_id
            response = client.get("/api/v1/agents", params=params)

            assert response.status_code == 200
            mock_agent_service.list_agents.assert_called_once_with(
                project_id=test_project.id,
                model=None,
                is_default=True,
                bound_device_id=UUID(device_id) if device_id else None,
                limit=10,
                offset=5,
            )
        finally:
            if get_agent_service in app.dependency_overrides:
                del app.dependency_overrides[get_agent_service]

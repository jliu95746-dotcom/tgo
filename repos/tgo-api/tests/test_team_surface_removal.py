"""Tests covering removed team-oriented tgo-api surfaces."""

from __future__ import annotations

from app.core.dev_data import DEFAULT_PERMISSIONS, DEFAULT_USER_GLOBAL_PERMISSIONS
import app.schemas as app_schemas


def test_ai_teams_routes_absent(client) -> None:
    """OpenAPI should not publish the removed AI team routes."""

    schema = client.get("/v1/openapi.json").json()

    assert all(not path.startswith("/v1/ai/teams") for path in schema["paths"])


def test_team_schema_exports_absent() -> None:
    """tgo-api should not keep legacy team schemas in its public exports."""

    removed_exports = (
        "TeamCreateRequest",
        "TeamUpdateRequest",
        "TeamResponse",
        "TeamListResponse",
        "TeamWithDetailsResponse",
    )

    assert all(not hasattr(app_schemas, name) for name in removed_exports)


def test_team_permissions_absent_from_seed_data() -> None:
    """Default permissions should no longer advertise AI team resources."""

    assert all(resource != "ai_teams" for resource, _action, _desc in DEFAULT_PERMISSIONS)
    assert all(resource != "ai_teams" for resource, _action in DEFAULT_USER_GLOBAL_PERMISSIONS)

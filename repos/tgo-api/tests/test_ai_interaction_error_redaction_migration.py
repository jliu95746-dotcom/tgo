"""Legacy AI run failures are reduced to non-sensitive codes on upgrade."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _migration():
    path = Path(__file__).parents[1] / (
        "alembic/versions/0078_ai_interaction_error_redaction.py"
    )
    spec = spec_from_file_location("ai_interaction_error_redaction", path)
    assert spec is not None and spec.loader is not None
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_ai_interaction_error_redaction_replaces_legacy_details() -> None:
    migration = _migration()
    assert migration.down_revision == "0077_visitor_im_disconnect_lease"
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE api_ai_interaction_runs "
            "(id INTEGER PRIMARY KEY, error_message TEXT)"
        ))
        samples = [
            (1, None),
            (2, ""),
            (3, "provider response with customer-phone-13800000000"),
            (4, "visitor_access_ended"),
            (5, "ai_workflow_failed"),
            (6, "AI stream cancelled before completion"),
            (7, "Background AI interaction was cancelled"),
            (8, "客户业务会话无法确认，未执行查询"),
        ]
        connection.execute(text(
            "INSERT INTO api_ai_interaction_runs (id, error_message) "
            "VALUES (:id, :error_message)"
        ), [
            {"id": row_id, "error_message": value}
            for row_id, value in samples
        ])
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert connection.execute(text(
                "SELECT id, error_message FROM api_ai_interaction_runs "
                "ORDER BY id"
            )).all() == [
                (1, None),
                (2, None),
                (3, "ai_processing_failed"),
                (4, "visitor_access_ended"),
                (5, "ai_workflow_failed"),
                (6, "ai_stream_cancelled"),
                (7, "ai_background_cancelled"),
                (8, "business_context_unavailable"),
            ]
            migration.downgrade()
    engine.dispose()

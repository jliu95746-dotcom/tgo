"""Removed personal WeChat must not remain available through public APIs."""

import pytest
from pydantic import ValidationError

from app.models.platform import PlatformType
from app.schemas import PlatformCreate, PlatformUpdate
from app.services.platform_type_seed import SEED_PLATFORM_TYPES


def test_personal_wechat_is_not_a_platform_type():
    assert "wechat_personal" not in {item.value for item in PlatformType}
    assert "wechat_personal" not in {item["type"] for item in SEED_PLATFORM_TYPES}


@pytest.mark.parametrize("schema", [PlatformCreate, PlatformUpdate])
def test_personal_wechat_cannot_be_created_or_selected(schema):
    with pytest.raises(ValidationError):
        schema(type="wechat_personal")


def test_personal_wechat_console_routes_are_removed(client):
    paths = client.get("/v1/openapi.json").json()["paths"]
    assert not any("/vision-" in path for path in paths)


@pytest.mark.parametrize("platform_type", ["wecom", "wecom_bot", "wechat", "website"])
def test_other_wechat_and_website_channels_are_preserved(platform_type):
    assert PlatformCreate(type=platform_type, name='测试渠道').type.value == platform_type


def test_retirement_migration_preserves_records_and_other_channels():
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    from sqlalchemy import create_engine, text

    migration = Path(__file__).parents[1] / "alembic/versions/0036_retire_personal_wechat.py"
    spec = importlib.util.spec_from_file_location("retire_personal", migration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE api_platforms (type TEXT, is_active BOOLEAN, ai_mode TEXT, sync_status TEXT, sync_retry_count INTEGER, updated_at TEXT)"))
        connection.execute(text("CREATE TABLE api_platform_types (type TEXT, is_supported BOOLEAN)"))
        for channel in ("wechat_personal", "wecom", "wechat"):
            connection.execute(text("INSERT INTO api_platforms VALUES (:channel, true, 'auto', 'synced', 0, NULL)"), {"channel": channel})
            connection.execute(text("INSERT INTO api_platform_types VALUES (:channel, true)"), {"channel": channel})
        module.op = SimpleNamespace(execute=connection.execute)
        module.upgrade()
        rows = connection.execute(text("SELECT type, is_active, ai_mode FROM api_platforms ORDER BY type")).all()
        assert len(rows) == 3
        assert ("wechat_personal", 0, "off") in rows
        assert ("wecom", 1, "auto") in rows
        assert ("wechat", 1, "auto") in rows
        module.downgrade()
        assert connection.scalar(text("SELECT is_active FROM api_platforms WHERE type='wechat_personal'")) == 0

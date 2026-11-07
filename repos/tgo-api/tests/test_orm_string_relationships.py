"""Resolve string joins before any model mapper has been configured."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_fresh_model_registry_resolves_string_relationships() -> None:
    script = """
import json
from sqlalchemy.orm import configure_mappers
from app.core.database import Base
from app.models.platform import Platform, PlatformTypeDefinition
from app.models.visitor import Visitor
mappers = list(Base.registry.mappers)
assert mappers and not any(mapper.configured for mapper in mappers)
configure_mappers()
assert all(mapper.configured for mapper in mappers)
relationships = {
    "type-platforms": PlatformTypeDefinition.platforms.property,
    "platform-visitors": Platform.visitors.property,
    "platform-type": Platform.platform_type.property,
    "visitor-platform": Visitor.platform.property,
}
print(json.dumps({key: str(value.primaryjoin)
                  for key, value in relationships.items()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "SECRET_KEY": "test-secret-key-for-pytest-only-1234567890",
            "DATABASE_URL": "postgresql://test:synthetic@127.0.0.1:1/test",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "type-platforms": "api_platform_types.type = api_platforms.type",
        "platform-visitors": "api_platforms.id = api_visitors.platform_id",
        "platform-type": "api_platforms.type = api_platform_types.type",
        "visitor-platform": "api_visitors.platform_id = api_platforms.id",
    }

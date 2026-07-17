from __future__ import annotations

import os


os.environ.setdefault("API_BASE_URL", "http://tgo-api:8000")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/tgo_platform_test",
)

import pytest
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.database import Base
from app.config import settings

# Use a test database or an in-memory sqlite if possible, 
# but for now we'll just mock the session if needed.
# For unit tests we usually don't need the DB.

@pytest.fixture
def mock_context_data():
    return {
        "start.input": "hello",
        "llm_1.text": "world"
    }


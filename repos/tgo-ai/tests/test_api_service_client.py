from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.services.api_service import APIServiceClient


@pytest.mark.asyncio
async def test_store_credential_is_cached_between_agent_builds() -> None:
    """Repeated chat turns should not refetch the same local credential."""

    response = Mock(status_code=200)
    response.json.return_value = {"api_key": "masked-test-key"}
    get = AsyncMock(return_value=response)
    client = APIServiceClient()
    client._http_client = Mock(get=get, is_closed=False)

    first = await client.get_store_credential("project-1")
    second = await client.get_store_credential("project-1")

    assert first == second
    assert get.await_count == 1

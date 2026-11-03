from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.services.wukongim_client import WuKongIMClient


@pytest.mark.asyncio
async def test_known_missing_message_is_absent_even_when_im_uses_http_200():
    client = WuKongIMClient()
    client.enabled = True
    client._make_request = AsyncMock(return_value={"status": 404})
    result = await client.get_message_by_client_msg_no(
        channel_id="test-customer-vtr",
        channel_type=251,
        client_msg_no="native-source-one",
        raise_on_error=True,
    )
    assert result is None


@pytest.mark.asyncio
async def test_unknown_im_response_is_not_mistaken_for_absence():
    client = WuKongIMClient()
    client.enabled = True
    client._make_request = AsyncMock(return_value={"status": 500})
    with pytest.raises(ValidationError):
        await client.get_message_by_client_msg_no(
            channel_id="test-customer-vtr",
            channel_type=251,
            client_msg_no="native-source-one",
            raise_on_error=True,
        )

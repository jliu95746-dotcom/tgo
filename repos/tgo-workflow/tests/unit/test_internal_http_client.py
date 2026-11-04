"""Private service checks never borrow environment proxies or another loop."""

import asyncio
from app.integrations.http_client import HttpClient


def test_internal_clients_close_before_each_task_loop_finishes():
    clients = []

    async def check():
        async with HttpClient.internal_client() as client:
            assert client.trust_env is False
            clients.append(client)
        assert client.is_closed

    asyncio.run(check())
    asyncio.run(check())
    assert clients[0] is not clients[1]

import httpx
from app.core.logging import logger


class HttpClient:
    _client: httpx.AsyncClient | None = None

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            logger.info("Initializing global HTTP client")
            cls._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                limits=httpx.Limits(
                    max_keepalive_connections=20, max_connections=100,
                )
            )
        return cls._client

    @classmethod
    async def close_client(cls):
        client = cls._client
        cls._client = None
        if client and not client.is_closed:
            logger.info("Closing global HTTP client")
            await client.aclose()


async def get_http_client() -> httpx.AsyncClient:
    return await HttpClient.get_client()

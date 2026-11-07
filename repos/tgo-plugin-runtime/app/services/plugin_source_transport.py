"""Read bounded public plugin sources without trusting request-time DNS."""

import asyncio

import httpx

from app.domain.business_tools.target_transport import (
    Resolver,
    _public,
    public_addresses,
)

SOURCE_FETCH_TIMEOUT = 10.0
MAX_SOURCE_BYTES = 1_048_576
MAX_SOURCE_REDIRECTS = 5


class PluginSourceRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Plugin source could not be safely fetched")


class PluginSourceTransport(httpx.AsyncBaseTransport):
    """Check destinations while retaining Host and certificate checks."""

    def __init__(
        self,
        *,
        resolver: Resolver = public_addresses,
        inner: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = MAX_SOURCE_BYTES,
    ) -> None:
        if max_response_bytes < 1:
            raise PluginSourceRejected()
        self.resolver = resolver
        self.inner = inner or httpx.AsyncHTTPTransport(
            trust_env=False,
            retries=0,
        )
        self.maximum = max_response_bytes

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        url = request.url
        if (
            request.method != "GET"
            or url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
        ):
            raise PluginSourceRejected()
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            addresses = await self.resolver(url.host, port)
        except (OSError, ValueError):
            addresses = []
        if (
            not addresses
            or len(addresses) > 32
            or not all(_public(address) for address in addresses)
        ):
            raise PluginSourceRejected()
        headers = request.headers.copy()
        headers["Host"] = url.netloc.decode()
        headers["Accept-Encoding"] = "identity"
        pinned = httpx.Request(
            "GET",
            url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        response = await self.inner.handle_async_request(pinned)
        try:
            # Redirect and failure bodies are never needed to parse a source.
            if response.status_code != 200:
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=b"",
                    extensions=response.extensions,
                )
            if (
                response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise PluginSourceRejected()
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdecimal()
                or len(declared) > 20
                or int(declared) > self.maximum
            ):
                raise PluginSourceRejected()
            chunks: list[bytes] = []
            count = 0
            if response.is_stream_consumed:
                if len(response.content) > self.maximum:
                    raise PluginSourceRejected()
                chunks.append(response.content)
            else:
                async for chunk in response.aiter_raw():
                    count += len(chunk)
                    if count > self.maximum:
                        raise PluginSourceRejected()
                    chunks.append(chunk)
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
                extensions=response.extensions,
            )
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self.inner.aclose()


async def fetch_plugin_source(
    url: str | httpx.URL,
    *,
    timeout: float = SOURCE_FETCH_TIMEOUT,
    resolver: Resolver = public_addresses,
    max_redirects: int = MAX_SOURCE_REDIRECTS,
) -> httpx.Response:
    """DNS, redirects and response reads share one source-attempt deadline."""
    async with asyncio.timeout(timeout):
        async with httpx.AsyncClient(
            transport=PluginSourceTransport(resolver=resolver),
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            target = httpx.URL(url)
            visited: set[str] = set()
            for _ in range(max_redirects + 1):
                if str(target) in visited:
                    raise PluginSourceRejected()
                visited.add(str(target))
                response = await client.get(target)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    return response
                location = response.headers.get("Location")
                if not location:
                    raise PluginSourceRejected()
                next_target = response.url.join(location)
                if target.scheme == "https" and next_target.scheme != "https":
                    raise PluginSourceRejected()
                target = next_target
            raise PluginSourceRejected()

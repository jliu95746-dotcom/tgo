"""Read bounded public pages using verified addresses on every redirect."""

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from ipaddress import IPv6Address, ip_address
import logging
import socket

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]
_private_fetch: ContextVar[bool] = ContextVar(
    "website_metadata_fetch", default=False
)


class _WebsiteLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _private_fetch.get():
            record.msg, record.args = "Website HTTP request completed", ()
        return True


logging.getLogger("httpx").addFilter(_WebsiteLogFilter())


class WebsiteTargetRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("Website target must be a public HTTP or HTTPS URL")


class WebsiteResponseRejected(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "Website returned an unsupported or oversized response"
        )


async def public_addresses(host: str, port: int) -> list[str]:
    try:
        return [str(ip_address(host))]
    except ValueError:
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return list(dict.fromkeys(str(row[4][0]) for row in rows))


def _public_address(value: str) -> bool:
    try:
        address = ip_address(value)
        if (
            "%" in value
            or not address.is_global
            or address.is_multicast
            or address.is_reserved
        ):
            return False
        if isinstance(address, IPv6Address):
            mapped = address.ipv4_mapped or address.sixtofour
            if mapped is not None and not mapped.is_global:
                return False
            if address.teredo is not None and not all(
                part.is_global for part in address.teredo
            ):
                return False
        return True
    except ValueError:
        return False


class PublicWebsiteTransport(httpx.AsyncBaseTransport):
    """Connect to a checked address while keeping original Host and TLS SNI."""

    def __init__(
        self,
        *,
        resolver: Resolver = public_addresses,
        inner: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = 1_048_576,
    ) -> None:
        if max_response_bytes < 1:
            raise ValueError("Response limit must be positive")
        self.resolver = resolver
        self.inner = inner or httpx.AsyncHTTPTransport(
            trust_env=False, retries=0
        )
        self.maximum = max_response_bytes

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        url = request.url
        if (
            request.method != "GET"
            or url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
        ):
            raise WebsiteTargetRejected()
        port = url.port or (443 if url.scheme == "https" else 80)
        try:
            addresses = await self.resolver(url.host, port)
        except (OSError, ValueError):
            raise httpx.ConnectError("Website DNS lookup failed") from None
        if (
            not addresses
            or len(addresses) > 32
            or not all(_public_address(address) for address in addresses)
        ):
            raise WebsiteTargetRejected()
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
            if (
                response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise WebsiteResponseRejected()
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdecimal()
                or len(declared) > 20
                or int(declared) > self.maximum
            ):
                raise WebsiteResponseRejected()
            chunks: list[bytes] = []
            count = 0
            if response.is_stream_consumed:
                if len(response.content) > self.maximum:
                    raise WebsiteResponseRejected()
                chunks.append(response.content)
            else:
                async for chunk in response.aiter_raw():
                    count += len(chunk)
                    if count > self.maximum:
                        raise WebsiteResponseRejected()
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


async def fetch_website(
    url: str,
    *,
    timeout: float,
    user_agent: str,
    max_redirects: int = 5,
) -> httpx.Response:
    """Keep redirects, DNS and body reads within one overall deadline."""
    token = _private_fetch.set(True)
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                transport=PublicWebsiteTransport(),
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                headers={"User-Agent": user_agent},
            ) as client:
                target = httpx.URL(url)
                visited: set[str] = set()
                for _ in range(max_redirects + 1):
                    if str(target) in visited:
                        raise WebsiteResponseRejected()
                    visited.add(str(target))
                    response = await client.get(target)
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        response.raise_for_status()
                        return response
                    location = response.headers.get("Location")
                    if not location:
                        raise WebsiteResponseRejected()
                    try:
                        target = response.url.join(location)
                    except (httpx.InvalidURL, ValueError):
                        raise WebsiteTargetRejected() from None
                raise WebsiteResponseRejected()
    except TimeoutError:
        raise httpx.TimeoutException("Website request timed out") from None
    finally:
        _private_fetch.reset(token)

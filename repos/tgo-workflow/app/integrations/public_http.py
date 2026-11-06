"""Bound a generic workflow GET to one verified public HTTPS destination."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from ipaddress import IPv6Address, ip_address

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]
MAX_API_RESPONSE_BYTES = 1024 * 1024
_private_query: ContextVar[bool] = ContextVar(
    "workflow_private_query", default=False
)


class _PrivateQueryLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _private_query.get():
            record.msg, record.args = (
                "Workflow API HTTP operation completed",
                (),
            )
        return True


logging.getLogger("httpx").addFilter(_PrivateQueryLogFilter())


class PublicTargetRejected(PermissionError):
    def __init__(self) -> None:
        super().__init__("API 查询目标或响应无法安全核验")


async def public_addresses(host: str, port: int) -> list[str]:
    try:
        return [str(ip_address(host))]
    except ValueError:
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return list(dict.fromkeys(str(row[4][0]) for row in rows))


def _is_public_address(value: str) -> bool:
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


class PinnedPublicGetTransport(httpx.AsyncBaseTransport):
    """Resolve once, reject private answers, then connect to the checked IP."""

    def __init__(
        self,
        target_url: str,
        *,
        resolver: Resolver = public_addresses,
        inner: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = MAX_API_RESPONSE_BYTES,
    ) -> None:
        try:
            self.target = httpx.URL(target_url)
        except httpx.InvalidURL:
            raise PublicTargetRejected() from None
        self.target_path = self.target.raw_path.split(b"?")[0]
        self.resolver = resolver
        self.maximum = max_response_bytes
        if (
            self.target.scheme != "https"
            or not self.target.host
            or self.target.userinfo
            or self.target.fragment
            or (self.target.port or 443) != 443
            or self.maximum < 1
        ):
            raise PublicTargetRejected()
        self.inner = inner or httpx.AsyncHTTPTransport(
            trust_env=False, retries=0
        )

    async def handle_async_request(
        self, request: httpx.Request
    ) -> httpx.Response:
        if (
            request.method != "GET"
            or request.url.scheme != "https"
            or request.url.host != self.target.host
            or (request.url.port or 443) != 443
            or request.url.raw_path.split(b"?")[0] != self.target_path
        ):
            raise PublicTargetRejected()
        try:
            addresses = await self.resolver(self.target.host, 443)
        except (OSError, ValueError):
            raise PublicTargetRejected() from None
        if (
            not addresses
            or len(addresses) > 32
            or not all(_is_public_address(address) for address in addresses)
        ):
            raise PublicTargetRejected()

        headers = request.headers.copy()
        headers["Host"] = self.target.netloc.decode()
        headers["Accept-Encoding"] = "identity"
        pinned = httpx.Request(
            "GET",
            request.url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions={
                **request.extensions,
                "sni_hostname": self.target.host,
            },
        )
        response = await self.inner.handle_async_request(pinned)
        try:
            if (
                300 <= response.status_code < 400
                or response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise PublicTargetRejected()
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdecimal() or int(declared) > self.maximum
            ):
                raise PublicTargetRejected()
            chunks: list[bytes] = []
            size = 0
            if response.is_stream_consumed:
                if len(response.content) > self.maximum:
                    raise PublicTargetRejected()
                chunks.append(response.content)
            else:
                async for chunk in response.aiter_raw():
                    size += len(chunk)
                    if size > self.maximum:
                        raise PublicTargetRejected()
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


async def bounded_api_get(
    url: str,
    headers: dict[str, str],
    params: dict[str, str],
    *,
    resolver: Resolver = public_addresses,
    inner: httpx.AsyncBaseTransport | None = None,
    max_response_bytes: int = MAX_API_RESPONSE_BYTES,
) -> httpx.Response:
    """
    Return a bounded response without exposing the URL in HTTP client logs.
    """
    transport = PinnedPublicGetTransport(
        url,
        resolver=resolver,
        inner=inner,
        max_response_bytes=max_response_bytes,
    )
    token = _private_query.set(True)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30.0),
        ) as client:
            return await client.get(url, headers=headers, params=params)
    except httpx.TimeoutException:
        raise TimeoutError("API 查询超时") from None
    except httpx.RequestError:
        raise RuntimeError("API 查询失败") from None
    finally:
        _private_query.reset(token)

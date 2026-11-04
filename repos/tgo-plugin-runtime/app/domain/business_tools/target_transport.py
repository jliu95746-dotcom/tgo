"""A configured read query cannot select another origin or an unbounded body."""

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from ipaddress import ip_address, IPv6Address
import logging
import socket

import httpx

Resolver = Callable[[str, int], Awaitable[list[str]]]
_private_query: ContextVar[bool] = ContextVar(
    "business_private_query_log", default=False
)


class _QueryLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _private_query.get():
            record.msg, record.args = "Business API HTTP operation completed", ()
        return True


logging.getLogger("httpx").addFilter(_QueryLogFilter())


@contextmanager
def business_query_log_scope() -> Iterator[None]:
    token = _private_query.set(True)
    try:
        yield
    finally:
        _private_query.reset(token)


class BusinessTargetRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("业务接口请求目标或响应无法安全核验")


async def safe_business_response(
    pending_response: Awaitable[httpx.Response],
) -> httpx.Response:
    """Remove request URLs and provider text from transport failures."""
    timed_out = False
    try:
        return await pending_response
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError:
        pass

    # Raise outside the handler so no request containing customer data remains
    # in the exception context seen by upstream diagnostics.
    if timed_out:
        raise TimeoutError("business query timed out")
    raise RuntimeError("business provider request failed")


async def public_addresses(host: str, port: int) -> list[str]:
    try:
        literal = ip_address(host)
        return [str(literal)]
    except ValueError:
        rows = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        return list(dict.fromkeys(str(row[4][0]) for row in rows))


def _public(value: str) -> bool:
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


class PinnedBusinessTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        origin: str,
        paths: list[str],
        *,
        resolver: Resolver = public_addresses,
        inner: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int = 1_048_576,
    ):
        self.origin = httpx.URL(origin)
        self.paths = frozenset(
            httpx.URL(origin + path).raw_path.split(b"?")[0] for path in paths
        )
        self.resolver = resolver
        self.inner = inner or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        self.maximum = max_response_bytes
        if (
            self.origin.scheme != "https"
            or (self.origin.port or 443) != 443
            or self.maximum < 1
        ):
            raise BusinessTargetRejected()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if (
            request.method not in {"GET", "POST"}
            or request.url.scheme != self.origin.scheme
            or request.url.host != self.origin.host
            or (request.url.port or 443) != 443
            or request.url.raw_path.split(b"?")[0] not in self.paths
        ):
            raise BusinessTargetRejected()
        try:
            addresses = await self.resolver(self.origin.host, 443)
        except (OSError, ValueError):
            raise BusinessTargetRejected() from None
        if (
            not addresses
            or len(addresses) > 32
            or not all(_public(address) for address in addresses)
        ):
            raise BusinessTargetRejected()
        headers = request.headers.copy()
        headers["Host"] = self.origin.netloc.decode()
        headers["Accept-Encoding"] = "identity"
        extensions = {**request.extensions, "sni_hostname": self.origin.host}
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        response = await self.inner.handle_async_request(pinned)
        try:
            if (
                300 <= response.status_code < 400
                or response.headers.get("Content-Encoding", "identity").lower()
                != "identity"
            ):
                raise BusinessTargetRejected()
            declared = response.headers.get("Content-Length")
            if declared is not None and (
                not declared.isdecimal() or int(declared) > self.maximum
            ):
                raise BusinessTargetRejected()
            chunks: list[bytes] = []
            count = 0
            if response.is_stream_consumed:
                if len(response.content) > self.maximum:
                    raise BusinessTargetRejected()
                chunks.append(response.content)
            else:
                async for chunk in response.aiter_raw():
                    count += len(chunk)
                    if count > self.maximum:
                        raise BusinessTargetRejected()
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

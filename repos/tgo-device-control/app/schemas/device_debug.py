"""Credential-free, typed device service diagnostics."""

from typing_extensions import NotRequired, TypedDict


class DebugConfig(TypedDict):
    tcp_rpc_host: str
    tcp_rpc_port: int
    http_host: str
    http_port: int
    environment: str
    log_level: str


class TCPDebugStatus(TypedDict):
    is_serving: NotRequired[bool]
    sockets: NotRequired[list[str]]
    error: NotRequired[str]
    local_port_check: NotRequired[str]


class RedisDebugStatus(TypedDict):
    status: NotRequired[str]
    active_bind_codes: NotRequired[int]


class DeviceDebugResponse(TypedDict):
    config: DebugConfig
    tcp_server: TCPDebugStatus
    redis: RedisDebugStatus
    connections: list[dict[str, str | None]]

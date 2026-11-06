import json
import re
from collections.abc import Mapping
from ipaddress import ip_address
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit
from app.engine.nodes.base import BaseNodeExecutor
from app.engine.context import ExecutionContext
from app.integrations.public_http import bounded_api_get
from app.engine.nodes.registry import register_node


def api_node_target_error(value: object) -> str | None:
    """
    Reject dynamic origins and literal local targets before a generic GET.
    """
    invalid = "API 节点需要固定的 HTTPS 地址（443 端口），不能指向本地地址"
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(char.isspace() for char in value)
        or "\\" in value
    ):
        return invalid
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if parsed.username or parsed.password:
            return "url must not contain credentials"
        if (
            parsed.scheme != "https"
            or not host
            or port not in (None, 443)
            or parsed.fragment
            or "{{" in parsed.netloc
            or "}}" in parsed.netloc
        ):
            return invalid
        try:
            if not ip_address(host).is_global:
                return invalid
        except ValueError:
            if (
                not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", host)
                or host.endswith((".local", ".localhost"))
                or re.fullmatch(r"[0-9.]+", host)
            ):
                return invalid
    except ValueError:
        return invalid
    return None


def api_node_is_read_only(config: Mapping[str, object]) -> bool:
    """
    Only bodyless GET may run until confirmed writes have durable receipts.
    """
    return config.get("method", "GET") == "GET" and config.get(
        "body_type", "none"
    ) in (None, "none")


@register_node("api")
class APINodeExecutor(BaseNodeExecutor):
    async def execute(
        self, context: ExecutionContext
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        if not api_node_is_read_only(self.config):
            raise PermissionError(
                "API 节点当前仅支持无请求体的 GET；写入需要人工确认和幂等回执"
            )
        configured_url = self.config.get("url", "")
        target_error = api_node_target_error(configured_url)
        if target_error:
            raise PermissionError(target_error)
        url = context.resolve_template(configured_url)
        if (
            api_node_target_error(url)
            or urlsplit(url).netloc != urlsplit(configured_url).netloc
        ):
            raise PermissionError("API 节点仅允许固定公网 HTTPS 目标")

        headers = {
            h["key"]: context.resolve_template(h["value"])
            for h in self.config.get("headers", [])
        }
        params = {
            p["key"]: context.resolve_template(p["value"])
            for p in self.config.get("params", [])
        }

        response = await bounded_api_get(url, headers, params)
        if not response.is_success:
            raise RuntimeError(f"API 查询失败 (HTTP {response.status_code})")

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = response.text

        outputs = {
            "body": body,
            "status_code": response.status_code,
            # Response headers may contain cookies or provider credentials.
            "headers": {},
        }

        return outputs, None

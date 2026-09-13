"""Credential parsing and grouping must not widen an agent's access."""

from types import SimpleNamespace

import pytest

from app.runtime.tools.mcp_bindings import group_mcp_bindings
from app.schemas.mcp_connection import mcp_connection_headers


@pytest.mark.parametrize("headers", [
    {"Authorization": 123}, {"Authorization": "secret\nheader"},
    {"bad name": "secret"}, {"Authorization": "secret", "authorization": "other"},
    ["secret"],
])
def test_invalid_headers_fail_without_echoing_credentials(headers):
    with pytest.raises(ValueError) as raised:
        mcp_connection_headers({"headers": headers})
    assert str(raised.value) == "MCP 请求头配置无效"


def test_grouping_keeps_different_credentials_and_transports_separate():
    def tool(name, secret, transport="http", source="LOCAL"):
        return SimpleNamespace(tool_name=name, transport_type=transport, tool_source_type=source,
                               base_config={"headers": {"Authorization": secret}})
    bindings = [tool("a", "first"), tool("b", "first"), tool("c", "second"),
                tool("d", "first", "sse"), tool("e", "first", source="STORE")]
    groups = group_mcp_bindings({"https://fixture.test/mcp": bindings})
    assert [[tool.tool_name for tool in items] for _, items in groups] == [["a", "b"], ["c"], ["d"], ["e"]]


def test_empty_headers_remain_optional():
    assert mcp_connection_headers(None) == {}
    assert mcp_connection_headers({"timeout": 5}) == {}

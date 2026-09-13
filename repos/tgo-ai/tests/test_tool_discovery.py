"""Discovery uses real isolated servers, never calls business tools."""
import pytest
from pydantic import ValidationError
from app.schemas.tool_probe import MCPDiscoverRequest
from app.services.tool_probe import discover_mcp, connection_error
from tests.test_mcp_persisted_lifecycle import mcp_targets  # noqa: F401


@pytest.mark.asyncio
@pytest.mark.parametrize('transport', ['http', 'sse'])
async def test_discovery_lists_tools_with_schema_without_executing(mcp_targets, transport):
    start, calls = mcp_targets
    endpoint = start('discovery', transport, True)
    result = await discover_mcp(MCPDiscoverRequest(endpoint=endpoint, transport=transport, headers={'Authorization': 'Bearer discovery'}))
    assert result.success
    tool = next(tool for tool in result.tools if tool.name == 'query')
    assert 'tracking_no' in tool.input_schema['properties']
    assert calls == []
    failed = await discover_mcp(MCPDiscoverRequest(endpoint=endpoint, transport=transport, headers={'Authorization': 'Bearer incorrect-fixture-secret'}))
    assert not failed.success
    assert '401' in failed.error
    assert 'incorrect-fixture-secret' not in failed.error
    assert calls == []


@pytest.mark.parametrize('endpoint', ['file:///etc/passwd', 'https://user:password@example.test/mcp', 'not-a-url'])
def test_discovery_rejects_non_http_or_embedded_credentials(endpoint):
    with pytest.raises(ValidationError):
        MCPDiscoverRequest(endpoint=endpoint)


def test_connection_errors_do_not_leak_remote_text():
    assert 'fixture-secret' not in connection_error(RuntimeError('fixture-secret'))
    assert '超时' in connection_error(TimeoutError())

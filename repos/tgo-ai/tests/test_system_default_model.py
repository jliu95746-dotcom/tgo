from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from app.services.system_default_model import resolve_system_default_model

class RuntimeAgent(SimpleNamespace):
    def model_copy(self, update):
        return RuntimeAgent(**{**self.__dict__, **update})

def make_session(model='new-model', active=True):
    provider_id = uuid4()
    config = SimpleNamespace(default_chat_provider_id=provider_id, default_chat_model=model)
    provider = SimpleNamespace(provider_kind='openai_compatible', vendor='deepseek', api_base_url='https://example.test/v1', api_key='test-key', organization=None, timeout=30)
    results = []
    for value in [config, provider if active else None]:
        result = MagicMock(); result.scalar_one_or_none.return_value = value; results.append(result)
    return SimpleNamespace(execute=AsyncMock(side_effect=results))

@pytest.mark.asyncio
async def test_explicit_agent_model_does_not_read_or_change_defaults():
    db = make_session(); agent = RuntimeAgent(model='fixed', project_id=str(uuid4()))
    assert await resolve_system_default_model(db, agent) is agent
    db.execute.assert_not_called()

@pytest.mark.asyncio
async def test_follow_uses_latest_model_and_credentials_without_mutating_agent():
    agent = RuntimeAgent(model='__system_default__', project_id=str(uuid4()))
    for model in ['old-default', 'new-default']:
        db = make_session(model)
        resolved = await resolve_system_default_model(db, agent)
        assert resolved.model == model
        assert resolved.llm_provider_credentials.api_key == 'test-key'
        assert agent.model == '__system_default__'
        statement = db.execute.call_args_list[1].args[0].compile()
        assert agent.project_id in {str(value) for value in statement.params.values()}

@pytest.mark.asyncio
async def test_missing_or_disabled_default_fails_without_fallback():
    with pytest.raises(ValueError):
        await resolve_system_default_model(make_session(active=False), RuntimeAgent(model='__system_default__', project_id=str(uuid4())))

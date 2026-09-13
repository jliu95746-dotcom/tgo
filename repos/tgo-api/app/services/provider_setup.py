"""Read-only credential resolution and bounded model invocation tests."""
from dataclasses import dataclass
from urllib.parse import quote, urlparse
from uuid import UUID
import httpx
from fastapi import HTTPException
from pydantic import JsonValue
from sqlalchemy.orm import Session
from app.models import AIProvider
from app.schemas.provider_setup import ProviderConnectionDraft, ProviderModelProbe, ModelProbeResult
from app.utils.crypto import decrypt_str

ConfigValue = JsonValue


@dataclass
class Connection:
    provider: str
    base: str
    key: str
    config: dict[str, ConfigValue]


def resolve_connection(db: Session, project_id: UUID, draft: ProviderConnectionDraft) -> Connection:
    key = draft.api_key.get_secret_value().strip() if draft.api_key else ''
    base = draft.api_base_url.strip().rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, '请填写完整的 HTTP 或 HTTPS 接口地址，不要包含密钥或查询参数')
    config = draft.config or {}
    if draft.provider_id:
        item = db.query(AIProvider).filter(AIProvider.id == draft.provider_id, AIProvider.project_id == project_id, AIProvider.deleted_at.is_(None)).first()
        if not item:
            raise HTTPException(404, '模型服务不存在')
        if not key:
            # Never forward stored credentials to an edited destination silently.
            if base != (item.api_base_url or '').rstrip('/') or draft.provider != item.provider:
                raise HTTPException(422, '接口地址或服务类型已修改，请重新填写密钥后获取模型或测试')
            key = decrypt_str(item.api_key) if item.api_key else ''
    if not key and draft.provider != 'ollama':
        raise HTTPException(422, '请填写 API 密钥')
    return Connection(draft.provider, base, key, config)


def build_model_request(connection: Connection, draft: ProviderModelProbe) -> tuple[str, dict[str, str], dict[str, object]]:
    if draft.model_type not in ('chat', 'embedding'):
        raise HTTPException(422, '此类型需要图片或音频样本，暂不支持一键测试；不会标记为测试成功')
    provider, base = connection.provider, connection.base
    headers = {'Authorization': f'Bearer {connection.key}'} if connection.key else {}
    suffix = 'embeddings' if draft.model_type == 'embedding' else 'chat/completions'
    body: dict[str, object] = {'model': draft.model_id}
    if draft.model_type == 'embedding':
        body['input'] = '连接测试'
    else:
        body.update(messages=[{'role': 'user', 'content': 'Reply with OK.'}], stream=False, max_tokens=16)
    if provider in ('azure', 'azure_openai'):
        headers = {'api-key': connection.key}
        version = quote(str(connection.config.get('api_version') or '2024-02-15-preview'), safe='')
        root = base if base.endswith('/openai') else base + '/openai'
        return f'{root}/deployments/{quote(draft.model_id, safe="")}/{suffix}?api-version={version}', headers, body
    if provider == 'ollama':
        base = base if base.endswith('/v1') else base + '/v1'
    if provider not in ('openai', 'deepseek', 'dashscope', 'moonshot', 'baichuan', 'custom', 'openai_compatible', 'ollama'):
        raise HTTPException(422, '此服务暂不支持一键调用测试，原有配置保持不变')
    return f'{base}/{suffix}', headers, body


async def run_model_probe(connection: Connection, draft: ProviderModelProbe, client: httpx.AsyncClient) -> ModelProbeResult:
    url, headers, body = build_model_request(connection, draft)
    try:
        response = await client.post(url, headers=headers, json=body)
        if not response.is_success:
            return ModelProbeResult(success=False, message=f'调用失败：HTTP {response.status_code}，请检查密钥、模型权限和接口地址')
        data = response.json()
        if draft.model_type == 'embedding':
            valid = bool(data.get('data') and data['data'][0].get('embedding'))
        else:
            choices = data.get('choices') or []
            message = choices[0].get('message', {}) if choices else {}
            valid = bool(message.get('content') or message.get('reasoning_content'))
        return ModelProbeResult(success=valid, message='调用成功' if valid else '接口已响应，但没有返回有效的模型结果')
    except httpx.TimeoutException:
        return ModelProbeResult(success=False, message='调用超时，请稍后重试')
    except (httpx.RequestError, ValueError, KeyError, TypeError, AttributeError):
        return ModelProbeResult(success=False, message='调用失败，请检查网络、接口地址和响应格式')

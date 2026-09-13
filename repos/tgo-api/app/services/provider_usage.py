"""Prevent removal of models still used by defaults or AI employees."""
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.project_ai_config import ProjectAIConfig
from app.services.ai_client import ai_client


async def assert_provider_unused(db: Session, project_id: UUID, provider_id: UUID, model_ids: set[str] | None = None) -> None:
    config = db.query(ProjectAIConfig).filter(ProjectAIConfig.project_id == project_id, ProjectAIConfig.deleted_at.is_(None)).first()
    for kind in ('chat', 'embedding', 'asr', 'ocr', 'vlm'):
        if config and getattr(config, f'default_{kind}_provider_id') == provider_id:
            if model_ids is None or getattr(config, f'default_{kind}_model') in model_ids:
                raise HTTPException(409, '此服务或模型正在用于系统模型用途，请先更换用途设置')
    offset = 0
    while True:
        try:
            result = await ai_client.list_agents(project_id=str(project_id), limit=100, offset=offset)
            rows = result.get('data')
            if not isinstance(rows, list):
                raise ValueError('Unexpected agent response')
        except Exception:
            raise HTTPException(503, '暂时无法检查 AI 员工引用，为避免误删，请稍后重试') from None
        for agent in rows:
            bound = str(agent.get('llm_provider_id') or agent.get('ai_provider_id') or '')
            if bound == str(provider_id) and (model_ids is None or agent.get('model') in model_ids):
                raise HTTPException(409, '此服务或模型已被 AI 员工使用，请先修改员工的模型设置')
        if len(rows) < 100:
            return
        offset += len(rows)

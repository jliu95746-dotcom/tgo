"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import (
    agent_trials,
    agent_business_connections,
    agent_device_script_bindings,
    agent_device_script_trials,
    agent_device_script_cases,
    agent_device_script_runs,
    agent_device_script_selection,
    agent_business_trials,
    agent_drafts,
    agent_activation_policies,
    agent_activation_requests,
    agent_execution,
    agent_business_execution,
    agents,
    chat,
    intent_analysis,
    media_analysis,
    model_usage,
    llm_models,
    llm_providers,
    project_ai_configs,
    skills,
    tools,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(
    model_usage.router,
    prefix="/internal/model-usage",
    tags=["Internal model usage"],
)

# Include route modules

api_router.include_router(agents.router, prefix="/agents", tags=["Agents"])
api_router.include_router(
    agent_trials.router, prefix="/agents", tags=["Agent trials"]
)
api_router.include_router(
    agent_business_connections.router,
    prefix="/agents",
    tags=["Employee business connections"],
)
api_router.include_router(
    agent_device_script_bindings.router, prefix="/agents", tags=["员工脚本配置"]
)
api_router.include_router(
    agent_device_script_trials.router, prefix="/agents", tags=["员工脚本试验"]
)
api_router.include_router(
    agent_device_script_cases.router, prefix="/agents", tags=["员工脚本用例"]
)
api_router.include_router(
    agent_device_script_runs.router, prefix="/agents", tags=["员工脚本业务执行"]
)
api_router.include_router(
    agent_device_script_selection.router,
    prefix="/agents",
    tags=["员工脚本固定审批"],
)
api_router.include_router(
    agent_business_trials.router,
    prefix="/agents",
    tags=["Employee business trials"],
)
api_router.include_router(
    agent_drafts.router, prefix="/agent-drafts", tags=["Agent setup drafts"]
)
api_router.include_router(
    agent_activation_policies.router,
    prefix="/agents",
    tags=["Agent activation policies"],
)
api_router.include_router(
    agent_activation_requests.router,
    prefix="/agents",
    tags=["Agent activation requests"],
)
api_router.include_router(
    agent_execution.router, prefix="/agents", tags=["Private execution checks"]
)
api_router.include_router(
    agent_business_execution.router,
    prefix="/agents",
    tags=["Private business execution"],
)
api_router.include_router(
    tools.router
)  # tools router carries its own prefix/tags
api_router.include_router(chat.router, prefix="/chat", tags=["Chat"])
api_router.include_router(
    intent_analysis.router,
    prefix="/analysis",
    tags=["Analysis"],
)
api_router.include_router(
    media_analysis.router, prefix="/analysis", tags=["Analysis"]
)

api_router.include_router(
    llm_providers.router, prefix="/llm-providers", tags=["LLM Providers"]
)
api_router.include_router(
    llm_models.router, prefix="/llm-models", tags=["LLM Models"]
)
api_router.include_router(
    project_ai_configs.router,
    prefix="/project-ai-configs",
    tags=["Project AI Configs"],
)
api_router.include_router(skills.router, prefix="/skills", tags=["Skills"])

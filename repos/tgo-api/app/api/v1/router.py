"""Main API v1 router."""

from fastapi import APIRouter, Depends
from app.api.company_knowledge_access import require_knowledge_write
from app.api.company_configuration_access import require_configuration_write, require_operator_model_management
from app.api.v1.endpoints import billing, operations_billing, wechat_payments
from app.api.v1.endpoints import billing_support
from app.api.v1.endpoints import operations_tasks
from app.api.v1.endpoints import operations_shared_models
from app.api.v1.endpoints import operations_management
from app.api.v1.endpoints import trial_activation
from app.api.v1.endpoints import billing_refunds

from app.api.v1.endpoints import (
    ai_agents,
    ai_models,
    ai_skills,
    ai_tools,
    ai_workflows,
    conversations,
    device_control,
    docs,
    email,
    onboarding,
    operations,
    platforms,
    plugins,
    plugin_tools,
    projects,
    remote_agents,
    wukongim,
    wukongim_webhook,
    rag_collections,
    rag_files,
    rag_qa_pairs,
    rag_websites,
    sessions,
    staff,
    registration,
    company_email,
    company_membership,
    tags,
    visitors,
    visitor_assignment_rules,
    visitor_waiting_queue,
    chat,
    channels,
    search,
    ai_providers,
    ai_runs,
    setup,
    system,
    store,
    utils,
    message_analysis,
    knowledge_governance,
    knowledge_versions,
    customer_logistics,
)

api_router = APIRouter()
api_router.include_router(billing.router, prefix="/billing", tags=["Billing"])
api_router.include_router(operations_billing.router, prefix="/ops", tags=["Operations billing"])
api_router.include_router(operations_tasks.router, prefix="/ops", tags=["Operations tasks"])
api_router.include_router(operations_shared_models.router, prefix="/ops", tags=["Operations models"])
api_router.include_router(operations_management.router, prefix="/ops", tags=["Operations management"])
api_router.include_router(trial_activation.ops_router, prefix="/ops", tags=["Trial activation"])
api_router.include_router(trial_activation.company_router, prefix="/company", tags=["Trial activation"])
api_router.include_router(wechat_payments.router, tags=["WeChat payments"])
api_router.include_router(billing_support.router, prefix="/billing", tags=["Billing support"])
api_router.include_router(billing_support.ops_router, prefix="/ops", tags=["Operations support"])
api_router.include_router(billing_refunds.router, prefix="/ops", tags=["Operations refunds"])
api_router.include_router(billing_refunds.callback_router, tags=["Refund notification"])

api_router.include_router(operations.router, prefix="/ops", tags=["域见运营"])
api_router.include_router(company_email.router, prefix="/staff", tags=["企业邮箱"])
api_router.include_router(company_membership.router, prefix="/company", tags=["企业成员"])
api_router.include_router(company_membership.public_router, prefix="/company", tags=["企业成员"])

# Setup endpoints (no authentication required)
api_router.include_router(
    setup.router,
    prefix="/setup",
    tags=["Setup"]
)

# Include all endpoint routers
api_router.include_router(
    projects.router,
    prefix="/projects",
    tags=["Projects"]
)

# Onboarding endpoints (JWT auth, project_id from current_user)
api_router.include_router(
    onboarding.router,
    prefix="/onboarding",
    tags=["Onboarding"]
)

api_router.include_router(
    registration.router,
    prefix="/staff",
    tags=["Staff"]
)

api_router.include_router(
    staff.router,
    prefix="/staff",
    tags=["Staff"]
)

api_router.include_router(
    visitors.router,
    prefix="/visitors",
    tags=["Visitors"]
)

api_router.include_router(
    visitor_assignment_rules.router,
    prefix="/visitor-assignment-rules",
    tags=["Visitor Assignment Rules"]
)

api_router.include_router(
    visitor_waiting_queue.router,
    prefix="/visitor-waiting-queue",
    tags=["Visitor Waiting Queue"]
)

api_router.include_router(
    tags.router,
    prefix="/tags",
    tags=["Tags"]
)

api_router.include_router(
    platforms.router,
    prefix="/platforms",
    tags=["Platforms"]
)

api_router.include_router(
    ai_providers.router,
    dependencies=[Depends(require_configuration_write), Depends(require_operator_model_management)],
    prefix="/ai/providers",
    tags=["AI Providers"]
)

# RAG Service Proxy Endpoints
api_router.include_router(
    rag_collections.router,
    dependencies=[Depends(require_knowledge_write)],
    prefix="/rag/collections",
    tags=["RAG Collections"]
)

api_router.include_router(
    rag_files.router,
    dependencies=[Depends(require_knowledge_write)],
    prefix="/rag/files",
    tags=["RAG Files"]
)

api_router.include_router(
    rag_websites.router,
    dependencies=[Depends(require_knowledge_write)],
    prefix="/rag/websites",
    tags=["RAG Websites"]
)

api_router.include_router(
    rag_qa_pairs.router,
    dependencies=[Depends(require_knowledge_write)],
    prefix="/rag",
    tags=["RAG QA Pairs"]
)

api_router.include_router(
    knowledge_governance.router,
    dependencies=[Depends(require_knowledge_write)],
    prefix="/rag/knowledge-governance",
    tags=["Knowledge Governance"],
)

api_router.include_router(knowledge_versions.router, prefix="/rag/knowledge-versions", tags=["Knowledge Versions"], dependencies=[Depends(require_knowledge_write)])


api_router.include_router(
    ai_models.router,
    dependencies=[Depends(require_configuration_write)],
    prefix="/ai-models",
    tags=["AI Models"]
)

api_router.include_router(
    ai_agents.router,
    dependencies=[Depends(require_configuration_write)],
    prefix="/ai/agents",
    tags=["AI Agents"]
)

# AI Runs helper endpoints
api_router.include_router(
    ai_runs.router,
    prefix="/ai/runs",
    tags=["AI Runs"]
)

# AI Tools endpoints
api_router.include_router(
    ai_tools.router,
    dependencies=[Depends(require_configuration_write)],
    prefix="/ai/tools",
    tags=["AI Tools"]
)

# AI Skills endpoints
api_router.include_router(
    ai_skills.router,
    dependencies=[Depends(require_configuration_write)],
    prefix="/ai/skills",
    tags=["AI Skills"]
)

# AI Workflows endpoints
api_router.include_router(
    ai_workflows.router,
    dependencies=[Depends(require_configuration_write)],
    prefix="/ai/workflows",
    tags=["AI Workflows"]
)


# WuKongIM Public Endpoints
api_router.include_router(
    wukongim.router,
    prefix="/wukongim",
    tags=["WuKongIM"]
)


api_router.include_router(
    wukongim_webhook.router
)

# Email endpoints
api_router.include_router(
    email.router,
    prefix="/email",
    tags=["Email"]
)

api_router.include_router(
    chat.router,
    prefix="/chat",
    tags=["Chat"],
)

api_router.include_router(
    channels.router,
    prefix="/channels",
    tags=["Channels"],
)

api_router.include_router(
    conversations.router,
    prefix="/conversations",
    tags=["Conversations"],
)

api_router.include_router(
    sessions.router,
    prefix="/sessions",
    tags=["Sessions"],
)

api_router.include_router(
    search.router,
    prefix="/search",
    tags=["Search"],
)

# System information endpoints
api_router.include_router(
    system.router,
    prefix="/system",
    tags=["System"],
)

# Unified documentation endpoints
api_router.include_router(
    docs.router,
    tags=["Documentation"],
)

# Store endpoints
api_router.include_router(
    store.router,
    prefix="/store",
    tags=["Store"],
)

# Utility endpoints
api_router.include_router(
    utils.router,
    prefix="/utils",
    tags=["Utils"],
)

# Plugin endpoints
api_router.include_router(
    plugin_tools.router,
    prefix="/plugins/tools",
    tags=["Plugin Tools"],
)

api_router.include_router(
    plugins.router,
    prefix="/plugins",
    tags=["Plugins"],
)

# Device Control endpoints (proxy to tgo-device-control service)
api_router.include_router(
    device_control.router,
    prefix="/device-control",
    tags=["Device Control"],
)

# Platform-authenticated media and intent result persistence
api_router.include_router(
    message_analysis.router,
    prefix="/message-analysis",
    tags=["Message Analysis"],
)

api_router.include_router(
    customer_logistics.router,
    prefix="/logistics",
    tags=["Customer Logistics"],
)

# Remote Agents endpoints (manage remote agents from AgentOS)
api_router.include_router(
    remote_agents.router,
    prefix="/remote-agents",
    tags=["Remote Agents"],
)

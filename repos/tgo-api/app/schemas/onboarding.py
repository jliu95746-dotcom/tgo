"""Onboarding progress schemas."""

from datetime import datetime
from typing import List, Literal, NotRequired, Optional, TypedDict
from uuid import UUID

from pydantic import Field

from app.schemas.base import BaseSchema, TimestampMixin


class OnboardingStepDefinition(TypedDict):
    step_number: int
    step_name: str
    description: str
    description_zh: str
    route: str
    step_type: Literal["action", "notify"]
    title: NotRequired[str]
    title_zh: NotRequired[str]


# Step definitions
ONBOARDING_STEPS: list[OnboardingStepDefinition] = [
    {
        "step_number": 1,
        "step_name": "setup_ai_provider",
        (
            'description'
        ): (
            'Set up at least one AI provider (e.g., OpenAI, Anthropic)'
        ),
        "description_zh": "设置至少一个 AI 提供商（如 OpenAI、Anthropic）",
        "route": "/settings/providers",
        "step_type": "action",
    },
    {
        "step_number": 2,
        "step_name": "set_default_models",
        (
            'description'
        ): (
            'Configure default chat and embedding models in project settings'
        ),
        "description_zh": "在项目设置中配置默认的聊天和嵌入模型",
        "route": "/settings/providers",
        "step_type": "action",
    },
    {
        "step_number": 3,
        "step_name": "create_knowledge_base",
        "description": "Create a RAG collection (file, website, or QA type)",
        "description_zh": "创建一个 RAG 知识库（文件、网站或问答类型）",
        "route": "/knowledge",
        "step_type": "action",
    },
    {
        "step_number": 4,
        "step_name": "create_agent",
        "description": "Create an AI agent",
        "description_zh": "创建一个 AI 智能体",
        "route": "/ai/agents",
        "step_type": "action",
    },
    {
        "step_number": 5,
        "step_name": "start_chat",
        "title": "Start Chatting",
        "title_zh": "开始聊天",
        "description": (
            'Set up [third-party platform](/platforms) to enable chat or '
            'chat directly with [agents](/ai/agents)'
        ),
        "description_zh": "设置[第三方平台](/platforms)即可开启聊天或直接与[智能体](/ai/agents)聊天",
        "route": "/chat",
        "step_type": "notify",
    },
]


class OnboardingStepStatus(BaseSchema):
    """Status of a single onboarding step."""

    step_number: int = Field(..., ge=1, le=5, description="Step number (1-5)")
    step_name: str = Field(..., description="Step identifier name")
    is_completed: bool = Field(
        ..., description="Whether this step is completed"
    )
    title: Optional[str] = Field(
        default=None, description="Step title in English"
    )
    title_zh: Optional[str] = Field(
        default=None, description="Step title in Chinese (中文标题)"
    )
    description: str = Field(..., description="Step description in English")
    description_zh: str = Field(
        ..., description="Step description in Chinese (中文描述)"
    )
    route: str = Field(..., description="Frontend route path for this step")
    step_type: str = Field(..., description="Step type: 'action' or 'notify'")


class OnboardingProgressResponse(BaseSchema, TimestampMixin):
    """Response schema for onboarding progress."""

    id: UUID = Field(..., description="Onboarding progress record ID")
    project_id: UUID = Field(..., description="Associated project ID")
    steps: List[OnboardingStepStatus] = Field(
        ..., description="Status of each onboarding step"
    )
    current_step: int = Field(
        ...,
        ge=1,
        le=6,
        description=(
            'Current step number (1-5 for in-progress, 6 if all completed)'
        ),
    )
    progress_percentage: int = Field(
        ..., ge=0, le=100, description="Completion percentage (0-100)"
    )
    is_completed: bool = Field(
        ..., description="Whether all steps are completed or skipped"
    )
    completed_at: Optional[datetime] = Field(
        default=None, description="Timestamp when onboarding was completed"
    )


class SkipOnboardingRequest(BaseSchema):
    """Request schema for skipping onboarding step(s)."""

    step_number: Optional[int] = Field(
        default=None,
        ge=1,
        le=5,
        description=(
            'Step number to skip (1-5). If not provided, skips all steps.'
        ),
    )

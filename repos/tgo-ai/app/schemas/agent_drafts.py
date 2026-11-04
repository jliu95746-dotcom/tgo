"""Trusted saved setup snapshots; never an activation request."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent import AgentCreate
from app.schemas.agent_activation import AgentActivationSelection


class AgentDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    agent: AgentCreate
    activation_selection: AgentActivationSelection | None = None

    @model_validator(mode="after")
    def require_local_inactive(self) -> "AgentDraftRequest":
        agent = self.agent
        if (agent.is_active or agent.is_default or agent.is_remote_store_agent
            or agent.remote_agent_url or agent.store_agent_id
            or agent.agent_category != "normal" or not agent.name.strip()):
            raise ValueError("场景草稿只能保存待启用的普通员工")
        if any(tool.config or tool.permissions for tool in agent.tools or []):
            raise ValueError("工具授权必须通过服务端业务连接配置")
        for rows in (agent.collections or [], agent.workflows or [],
                     [str(tool.tool_id) for tool in agent.tools or []]):
            if len(rows) != len(set(rows)):
                raise ValueError("资源不可重复绑定")
        return self


class AgentDraftReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    created_by: UUID
    draft_id: UUID
    revision: int = Field(ge=1)
    agent_id: UUID
    is_active: bool
    can_enable: Literal[False] = False
    activation_selection: AgentActivationSelection | None = None

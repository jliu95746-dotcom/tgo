"""Input and dependency contracts for configured-agent device debugging."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeviceDebugChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: UUID
    agent_id: UUID = Field(description="选择已绑定此设备的 AI 员工")
    message: str = Field(min_length=1, max_length=10_000)
    system_prompt: str | None = Field(default=None, max_length=10_000)
    model: str | None = Field(default=None, description="兼容旧请求；必须与员工配置一致")
    max_iterations: None = Field(
        default=None, description="请在 AI 员工配置中设置工具调用上限"
    )

    @field_validator("message")
    @classmethod
    def nonblank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入要调试的任务")
        return value


class DebugDeviceBinding(BaseModel):
    id: UUID
    project_id: UUID
    status: str


class DebugAgentBinding(BaseModel):
    id: UUID
    bound_device_id: UUID | None = None
    model: str

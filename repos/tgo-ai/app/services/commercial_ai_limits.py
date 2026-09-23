"""Apply platform ceilings after merging customer agent settings."""

from app.config import settings
from app.runtime.tools.models import AgentConfig
from app.services.quota_authorization import metered_execution
from app.schemas.chat import ChatCompletionRequest


def constrain_chat(request: ChatCompletionRequest) -> ChatCompletionRequest:
    if not metered_execution.get():
        return request
    from app.services.platform_models import current_model

    platform = current_model()
    if platform is not None and request.model == "__system_default__":
        request = request.model_copy(update={"model": platform.model})
    approved = (
        [platform.model] if platform is not None else settings.saas_approved_models
    )
    if request.model not in approved:
        raise ValueError("该模型尚未由平台批准用于企业订阅")
    if (
        len(request.model_dump_json(exclude_none=True))
        > settings.saas_max_input_characters
    ):
        raise ValueError("本轮输入超过企业 AI 处理长度上限")
    return request.model_copy(
        update={
            "n": 1,
            "max_tokens": min(
                request.max_tokens or settings.saas_max_output_tokens,
                settings.saas_max_output_tokens,
            ),
            "max_tool_rounds": min(
                request.max_tool_rounds if request.max_tool_rounds is not None else 5,
                settings.saas_max_tool_calls,
            ),
        }
    )


def constrain_agent(config: AgentConfig, message: str) -> AgentConfig:
    if not metered_execution.get():
        return config
    from app.services.platform_models import current_model, credentials

    platform = current_model()
    approved = (
        [platform.model] if platform is not None else settings.saas_approved_models
    )
    if not config.model_name or config.model_name not in approved:
        raise ValueError("该模型尚未由平台批准用于企业订阅，请联系平台配置")
    if platform is not None:
        config = config.model_copy(
            update={"provider_credentials": credentials(platform)}
        )
    inputs = (
        message,
        config.system_prompt,
        config.system_message,
        config.expected_output,
    )
    if sum(len(value or "") for value in inputs) > settings.saas_max_input_characters:
        raise ValueError("本轮输入超过企业 AI 处理长度上限，请缩短消息或资料")
    return config.model_copy(
        update={
            "max_tokens": max(
                1,
                min(
                    config.max_tokens or settings.saas_max_output_tokens,
                    settings.saas_max_output_tokens,
                ),
            ),
            "tool_call_limit": max(
                0,
                min(
                    config.tool_call_limit
                    if config.tool_call_limit is not None
                    else settings.saas_max_tool_calls,
                    settings.saas_max_tool_calls,
                ),
            ),
            "num_history_runs": max(
                0,
                min(
                    config.num_history_runs
                    if config.num_history_runs is not None
                    else settings.saas_max_history_runs,
                    settings.saas_max_history_runs,
                ),
            ),
        }
    )

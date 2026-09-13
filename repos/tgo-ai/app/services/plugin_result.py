"""Keep plugin output data and genuine execution failures intact."""

import json

from pydantic import ValidationError

from app.schemas.plugin_execution import PluginExecutionResult


class PluginToolResultError(ValueError):
    """Plugin execution failed or returned an invalid result contract."""


def plugin_result_text(payload: object) -> str:
    try:
        result = PluginExecutionResult.model_validate(payload)
    except ValidationError:
        # Validation errors contain the original input; never expose it to AI.
        raise PluginToolResultError("Invalid plugin response format") from None
    if not result.success:
        raise PluginToolResultError(result.error or result.content or "工具执行失败")
    if result.data is not None:
        if result.content:
            return json.dumps({"content": result.content, "data": result.data}, ensure_ascii=False)
        return json.dumps(result.data, ensure_ascii=False)
    return result.content

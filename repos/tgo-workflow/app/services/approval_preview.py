"""Render one bounded approval preview and identify exactly what staff saw."""

import hashlib
import json
import re
from collections.abc import Mapping
from app.engine.context import ExecutionContext


_VARIABLE = re.compile(r"\{\{([^{}]+)\}\}")


def render_approval_preview(template: str, values: Mapping[str, object]) -> str:
    """Resolve explicit JSON fields to scalars; never render whole objects."""
    if not isinstance(template, str):
        raise ValueError("审批预览模板无效")
    context = ExecutionContext(dict(values))

    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = context.get_variable(name)
        if not name or not isinstance(value, (str, int, float, bool)):
            raise ValueError("审批预览引用了缺失或非文本的流程变量")
        rendered = str(value)
        if "{{" in rendered or "}}" in rendered:
            raise ValueError("审批预览变量包含未解析的模板标记")
        return rendered

    result = _VARIABLE.sub(replace, template).strip()
    if not result or len(result) > 500 or "{{" in result or "}}" in result:
        raise ValueError("审批预览为空、过长或包含未解析的变量")
    return result


def approval_preview_digest(approval_id: str, execution_id: str, summary: str) -> str:
    payload = json.dumps(
        ["workflow-approval-preview-v1", approval_id, execution_id, summary],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

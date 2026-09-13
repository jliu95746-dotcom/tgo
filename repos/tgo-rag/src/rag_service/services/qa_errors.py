"""Allowlisted QA failure reasons, safe for storage, task results and APIs."""

import re


def safe_exception_chain(error: BaseException) -> str:
    """Expose bounded exception types, never provider messages or credentials."""
    names: list[str] = []
    visited: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in visited and len(names) < 6:
        visited.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return ' > '.join(names)

QA_QUEUE_FAILURE = "后台任务未能入队，请检查 RAG worker 和 Redis 后重试。"
QA_UNKNOWN_FAILURE = "问答处理失败，请联系管理员检查 RAG worker 日志。"
_CONFIG_FAILURE = "当前项目未配置可用的向量模型，请配置后重试。"
_KEY_FAILURE = "向量模型缺少 API Key，请检查模型配置后重试。"
_URL_FAILURE = "向量模型缺少 base_url，请检查模型配置后重试。"
_VECTOR_FAILURE = "向量模型返回了无效向量，请检查模型和向量维度后重试。"
_SAFE_REASONS = frozenset({
    QA_QUEUE_FAILURE, QA_UNKNOWN_FAILURE, _CONFIG_FAILURE,
    _KEY_FAILURE, _URL_FAILURE, _VECTOR_FAILURE,
})


def safe_qa_failure(error: Exception | str) -> str:
    """Keep only known categories or an HTTP status; never copy provider bodies."""
    value = str(error)
    if value in _SAFE_REASONS:
        return value
    if "No active embedding configuration" in value:
        return _CONFIG_FAILURE
    if "Failed to queue QA processing" in value:
        return QA_QUEUE_FAILURE
    if "api_key" in value and "missing" in value:
        return _KEY_FAILURE
    if "base_url" in value and "missing" in value:
        return _URL_FAILURE
    if "Embedding" in value and any(
        term in value for term in ("dimension", "finite", "zero vector")
    ):
        return _VECTOR_FAILURE
    status = re.search(r"(?:HTTP|Error code:|status code)\s*([45]\d{2})\b", value)
    if status:
        return f"向量处理请求失败（HTTP {status[1]}），请检查模型服务后重试。"
    return QA_UNKNOWN_FAILURE

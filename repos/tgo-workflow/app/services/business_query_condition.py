"""Deterministic conditions permitted inside customer business-query flows."""

from collections.abc import Mapping


_OUTCOMES = frozenset({"passed", "failed"})
_ERROR_CODES = frozenset(
    {
        "denied",
        "timeout",
        "provider_failed",
        "audit_unavailable",
        "authority_changed",
        "not_found",
        "not_shipped",
        "authentication_failed",
        "business_failed",
    }
)
_ALLOWED_COMPARISONS = {
    "order.outcome": _OUTCOMES,
    "logistics.outcome": _OUTCOMES,
    "order.error_code": _ERROR_CODES,
    "logistics.error_code": _ERROR_CODES,
}


def business_query_condition_error(config: Mapping[str, object]) -> str | None:
    """Reject model calls, expressions, and raw customer input comparisons."""
    variable = config.get("variable")
    comparison = config.get("compare_value")
    if (
        config.get("condition_type") == "variable"
        and config.get("operator") == "equals"
        and isinstance(variable, str)
        and isinstance(comparison, str)
        and comparison in _ALLOWED_COMPARISONS.get(variable, frozenset())
    ):
        return None
    return "受控业务查询条件只允许将查询结果或错误码与固定值比较。"

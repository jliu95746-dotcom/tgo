"""
Public and persisted node evidence, separate from private runtime context.
"""

from uuid import UUID

from pydantic import JsonValue

from app.services.business_query_context import business_query_record_inputs


def node_receipt_output(
    node_type: str,
    outputs: dict[str, JsonValue],
    *,
    governed_query: bool,
) -> JsonValue:
    """
    Keep bounded trace evidence without copying arbitrary customer/provider
    data.
    """
    if node_type == "input" and governed_query:
        return business_query_record_inputs(outputs)
    if node_type in {"answer", "business_query"}:
        return outputs
    if node_type == "condition" and type(outputs.get("result")) is bool:
        return {"result": outputs["result"]}
    if node_type == "approval" and outputs.get("requested") is True:
        return {"requested": True}
    evidence: dict[str, JsonValue] = {
        "capture_status": "redacted",
        "field_count": len(outputs),
    }
    if node_type == "agent":
        child_execution_id = outputs.get("agent_execution_id")
        if isinstance(child_execution_id, str):
            try:
                evidence["agent_execution_id"] = str(UUID(child_execution_id))
            except ValueError:
                pass
    if node_type == "api" and type(outputs.get("status_code")) is int:
        evidence["status_code"] = outputs["status_code"]
    return evidence

"""The API proxy preserves the governed workflow node contract."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.ai_workflows import WorkflowNodeInput


def test_business_query_node_passes_through_api_proxy():
    connection_id = str(uuid4())
    node = WorkflowNodeInput.model_validate({
        "id": "query", "type": "business_query",
        "position": {"x": 100, "y": 100},
        "data": {
            "type": "business_query", "label": "查询订单",
            "reference_key": "order", "connection_id": connection_id,
            "operation": "order_query", "order_variable": "input.order_no",
        },
    })
    assert node.model_dump(mode="json")["data"]["connection_id"] == connection_id
    with pytest.raises(ValidationError):
        WorkflowNodeInput.model_validate({
            "id": "query", "type": "business_query", "position": {"x": 0, "y": 0},
            "data": {"type": "business_query", "label": "查询订单",
                     "reference_key": "order", "connection_id": "https://foreign.example.test",
                     "operation": "order_query", "order_variable": "input.order_no"},
        })

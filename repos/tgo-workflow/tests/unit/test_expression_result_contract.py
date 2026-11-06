"""Expression annotations must preserve actual values and branch truthiness."""

import pytest

from app.engine.context import ExecutionContext
from app.engine.nodes.condition import ConditionNodeExecutor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expression, value, handle",
    [
        ("1 + 1", 2, "true"),
        ("0", 0, "false"),
        ("''", "", "false"),
        ("'hello'", "hello", "true"),
        ("input.count > 2", True, "true"),
    ],
)
async def test_expression_keeps_value_and_handle(expression, value, handle):
    node = ConditionNodeExecutor(
        "condition",
        {
            "data": {
                "condition_type": "expression",
                "expression": expression,
            }
        },
    )
    output, actual_handle = await node.execute(
        ExecutionContext({"input.count": 3})
    )
    assert output == {"result": value}
    assert type(output["result"]) is type(value)
    assert actual_handle == handle

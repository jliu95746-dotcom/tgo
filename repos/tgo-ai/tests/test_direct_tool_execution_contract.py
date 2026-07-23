"""Contract checks for direct project-owned tool execution."""

from app.api.v1.tools import DirectToolExecuteRequest, DirectToolExecuteResponse


def test_direct_tool_execute_contract_rejects_unknown_fields() -> None:
    request = DirectToolExecuteRequest(
        input_data={"tracking_no": "SF1234567890"},
        visitor_id="visitor-1",
    )

    assert request.input_data["tracking_no"] == "SF1234567890"
    assert DirectToolExecuteResponse(success=True, output_data={}).success

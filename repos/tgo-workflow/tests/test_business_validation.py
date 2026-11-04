import pytest

from app.services.validation_service import ValidationService


def graph(node_type="agent", data=None):
    return {"nodes": [
        {"id": "i", "type": "input", "data": {"reference_key": "input"}},
        {"id": "a", "type": node_type, "data": data or {}},
        {"id": "o", "type": "answer", "data": {"output_type": "variable", "output_variable": "reply.text"}},
    ], "edges": [{"source": "i", "target": "a"}, {"source": "a", "target": "o"}]}


def test_missing_agent_is_rejected():
    assert any("agent_id" in error for error in ValidationService.validate_workflow(graph()))


def test_parallel_without_branches_and_unscheduled_timer_are_rejected():
    assert any("并行节点" in error for error in ValidationService.validate_workflow(graph("parallel")))
    definition = graph("agent", {"agent_id": "agent-1"})
    definition["nodes"][0]["type"] = "timer"
    assert any("not supported" in error for error in ValidationService.validate_workflow(definition))


def test_duplicate_ids_and_references_are_rejected():
    definition = graph("agent", {"agent_id": "a", "reference_key": "input"})
    assert any("reference_key" in error for error in ValidationService.validate_workflow(definition))
    definition["nodes"][1]["id"] = "i"
    assert any("Duplicate node" in error for error in ValidationService.validate_workflow(definition))


def test_valid_business_flow_passes():
    assert ValidationService.validate_workflow(graph("agent", {"agent_id": "a", "reference_key": "reply"})) == []


@pytest.mark.parametrize("pin", [
    {"expected_device_id": "bad"},
    {"expected_device_policy_digest": "a" * 64},
    {"expected_device_id": "00000000-0000-4000-8000-000000000001",
     "expected_device_policy_digest": "bad"},
])
def test_agent_device_policy_pin_requires_valid_complete_pair(pin):
    data = {"agent_id": "a", "reference_key": "reply", **pin}
    assert any("设备" in error for error in ValidationService.validate_workflow(graph("agent", data)))


def test_agent_device_policy_pin_accepts_complete_pair():
    data = {
        "agent_id": "a", "reference_key": "reply",
        "expected_device_id": "00000000-0000-4000-8000-000000000001",
        "expected_device_policy_digest": "a" * 64,
    }
    assert ValidationService.validate_workflow(graph("agent", data)) == []


def test_credentials_and_missing_endpoint_are_rejected():
    assert any("url" in error for error in ValidationService.validate_workflow(graph("api")))
    assert any("credentials" in error for error in ValidationService.validate_workflow(graph("api", {"url": "https://u:p@example.test"})))


@pytest.mark.parametrize("url", [
    "http://example.test/query", "https://127.0.0.1/query",
    "https://{{input.host}}/query", "https://example.test:8443/query",
])
def test_api_target_must_be_fixed_public_https(url):
    assert any("HTTPS" in error for error in ValidationService.validate_workflow(graph("api", {"url": url})))


def test_api_path_template_still_works_with_fixed_host():
    definition = graph("api", {"url": "https://orders.example.test/query/{{input.order_no}}"})
    definition["nodes"][2]["data"]["output_variable"] = "query.status_code"
    assert ValidationService.validate_workflow(definition) == []


def test_api_raw_body_cannot_be_returned_as_a_customer_answer():
    definition = graph("api", {"url": "https://orders.example.test/query", "reference_key": "query"})
    answer = definition["nodes"][2]["data"]
    answer["output_variable"] = "query.body"
    assert any("API 原始响应" in error for error in ValidationService.validate_workflow(definition))

    answer.update(output_type="template", output_template="订单：{{query.body}}")
    assert any("API 原始响应" in error for error in ValidationService.validate_workflow(definition))

    answer.update(output_type="structured", output_structure=[{"key": "order", "value": "{{query.body}}"}])
    assert any("API 原始响应" in error for error in ValidationService.validate_workflow(definition))

    answer.update(output_type="variable", output_variable="query.headers")
    assert any("API 原始响应" in error for error in ValidationService.validate_workflow(definition))


def test_api_status_code_does_not_trigger_raw_body_guard():
    definition = graph("api", {"url": "https://orders.example.test/query", "reference_key": "query"})
    definition["nodes"][2]["data"]["output_variable"] = "query.status_code"
    assert ValidationService.validate_workflow(definition) == []


def test_api_without_reference_key_cannot_bypass_raw_output_guard():
    definition = graph("api", {"url": "https://orders.example.test/query"})
    definition["nodes"][2]["data"]["output_variable"] = "None.body"
    assert any("API 原始响应" in error for error in ValidationService.validate_workflow(definition))

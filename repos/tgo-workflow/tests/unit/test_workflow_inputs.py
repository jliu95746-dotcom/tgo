"""Invalid customer inputs never start nodes or enqueue business work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api import executions
from app.engine.executor import WorkflowExecutor
from app.schemas.execution import WorkflowExecuteRequest
from app.services.validation_service import ValidationService
from app.services.workflow_inputs import WorkflowInputError, validate_inputs
from celery_app import tasks


def graph(variables=None):
    return {"nodes": [
        {"id": "i", "type": "input", "data": {"reference_key": "input", "input_variables": variables or [
            {"name": "order_number", "type": "string", "required": True},
        ]}},
        {"id": "o", "type": "answer", "data": {"reference_key": "answer", "output_type": "template", "output_template": "ok"}},
    ], "edges": [{"source": "i", "target": "o"}]}


@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_required_strings_need_actual_content(value):
    with pytest.raises(WorkflowInputError) as error:
        validate_inputs(graph(), {"order_number": value})
    assert error.value.issues[0].code == "required"


@pytest.mark.parametrize("kind,value", [
    ("number", True), ("number", "123"), ("number", float("nan")),
    ("number", float("inf")), ("boolean", "false"), ("boolean", 0),
    ("string", {"private_token": "DO_NOT_ECHO"}),
])
def test_types_are_strict_and_errors_do_not_echo_input(kind, value):
    with pytest.raises(WorkflowInputError) as error:
        validate_inputs(graph([{"name": "value", "type": kind}]), {"value": value})
    assert error.value.issues[0].code == "type"
    assert "DO_NOT_ECHO" not in str(error.value)
    assert "private_token" not in str(error.value.issues)


def test_false_zero_and_legacy_optional_inputs_are_preserved():
    definition = graph([
        {"name": "consent", "type": "boolean", "required": True},
        {"name": "quantity", "type": "number", "required": True},
        {"name": "legacy", "type": "string"},
    ])
    inputs = {"consent": False, "quantity": 0, "context_id": "existing"}
    assert validate_inputs(definition, inputs) == inputs
    assert validate_inputs(graph([{"name": "legacy", "type": "string"}]), {}) == {}


@pytest.mark.parametrize("value,code", [
    ("订", "length"), ("订购编号一二三四五六", "length"),
    ("订购一二", None),
])
def test_declared_text_length_is_checked_without_echoing_customer_text(value, code):
    definition = graph([{
        "name": "question", "type": "string", "required": True,
        "min_length": 2, "max_length": 6,
    }])
    if code is None:
        assert validate_inputs(definition, {"question": value}) == {"question": value}
        return
    with pytest.raises(WorkflowInputError) as error:
        validate_inputs(definition, {"question": value})
    assert error.value.issues[0].code == code
    assert value not in str(error.value.issues)


@pytest.mark.parametrize("value,code", [
    (-1, "range"), (11, "range"), (0, None), (10, None),
])
def test_declared_number_range_is_inclusive(value, code):
    definition = graph([{
        "name": "quantity", "type": "number", "required": True,
        "minimum": 0, "maximum": 10,
    }])
    if code is None:
        assert validate_inputs(definition, {"quantity": value}) == {"quantity": value}
        return
    with pytest.raises(WorkflowInputError) as error:
        validate_inputs(definition, {"quantity": value})
    assert error.value.issues[0].code == code


def test_declared_business_choices_reject_unknown_value_without_echoing_it():
    definition = graph([{
        "name": "reason", "type": "string", "required": True,
        "allowed_values": ["退货", "换货", "质量问题"],
    }])
    assert validate_inputs(definition, {"reason": "退货"}) == {"reason": "退货"}
    with pytest.raises(WorkflowInputError) as error:
        validate_inputs(definition, {"reason": "PRIVATE-CUSTOMER-TEXT"})
    assert error.value.issues[0].code == "choice"
    assert "PRIVATE-CUSTOMER-TEXT" not in str(error.value.issues)


@pytest.mark.parametrize("choices", [
    [], [""], [" 退货"], ["退货 ", "换货"], ["退货", "退货"],
    ["退货"] * 31, ["x" * 101],
])
def test_invalid_business_choices_cannot_be_published(choices):
    assert ValidationService.validate_workflow(graph([{
        "name": "reason", "type": "string", "allowed_values": choices,
    }]))


def test_business_choices_are_only_valid_for_text():
    assert ValidationService.validate_workflow(graph([{
        "name": "reason", "type": "number", "allowed_values": ["1"],
    }]))


@pytest.mark.parametrize("variable", [
    {"name": "v", "type": "string", "min_length": 3, "max_length": 2},
    {"name": "v", "type": "number", "minimum": 2, "maximum": 1},
    {"name": "v", "type": "boolean", "min_length": 1},
    {"name": "v", "type": "number", "max_length": 5},
    {"name": "v", "type": "string", "minimum": 0},
    {"name": "v", "type": "string", "max_length": -1},
])
def test_invalid_input_constraints_cannot_be_published(variable):
    assert ValidationService.validate_workflow(graph([variable]))


@pytest.mark.parametrize("variables", [
    [{"name": " ", "type": "string"}],
    [{"name": "value", "type": "object"}],
    [{"name": "value", "type": "string", "required": "false"}],
    [{"name": "value", "type": "string"}, {"name": "value", "type": "boolean"}],
])
def test_ambiguous_input_declarations_cannot_be_published(variables):
    assert ValidationService.validate_workflow(graph(variables))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"stream": True}, {"async": True}])
async def test_all_http_modes_reject_inputs_before_persisting_or_dispatching(monkeypatch, mode):
    monkeypatch.setattr(executions.WorkflowService, "get_by_id", AsyncMock(return_value=SimpleNamespace(definition=graph(), version=1)))
    create = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    monkeypatch.setattr(executions, "enqueue_workflow", dispatch)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow("owned-flow", WorkflowExecuteRequest(inputs={"order_number": {"secret": "DO_NOT_ECHO"}}, **mode), "owned-project", AsyncMock())
    assert error.value.status_code == 422
    assert error.value.detail["errors"][0]["field"] == "order_number"
    assert "DO_NOT_ECHO" not in str(error.value.detail)
    create.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"stream": True}, {"async": True}])
async def test_all_http_modes_enforce_declared_length_before_dispatch(monkeypatch, mode):
    definition = graph([{
        "name": "order_number", "type": "string", "required": True,
        "min_length": 4, "max_length": 8,
    }])
    monkeypatch.setattr(executions.WorkflowService, "get_by_id", AsyncMock(
        return_value=SimpleNamespace(definition=definition, version=1),
    ))
    create = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    monkeypatch.setattr(executions, "enqueue_workflow", dispatch)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "owned-flow", WorkflowExecuteRequest(
                inputs={"order_number": "PRIVATE-ORDER-123"}, **mode,
            ), "owned-project", AsyncMock(),
        )
    assert error.value.status_code == 422
    assert error.value.detail["errors"][0]["code"] == "length"
    assert "PRIVATE-ORDER-123" not in str(error.value.detail)
    create.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"stream": True}, {"async": True}])
async def test_all_http_modes_enforce_business_choices_before_dispatch(monkeypatch, mode):
    definition = graph([{
        "name": "reason", "type": "string", "required": True,
        "allowed_values": ["退货", "换货"],
    }])
    monkeypatch.setattr(executions.WorkflowService, "get_by_id", AsyncMock(
        return_value=SimpleNamespace(definition=definition, version=1),
    ))
    create = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    monkeypatch.setattr(executions, "enqueue_workflow", dispatch)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow(
            "owned-flow", WorkflowExecuteRequest(inputs={"reason": "PRIVATE-CUSTOMER-TEXT"}, **mode),
            "owned-project", AsyncMock(),
        )
    assert error.value.status_code == 422
    assert error.value.detail["errors"][0]["code"] == "choice"
    assert "PRIVATE-CUSTOMER-TEXT" not in str(error.value.detail)
    create.assert_not_awaited()
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_formal_inputs_use_selected_release_not_new_draft(monkeypatch):
    optional_draft = graph([{"name": "order_number", "type": "string"}])
    monkeypatch.setattr(executions.WorkflowService, "get_by_id", AsyncMock(return_value=SimpleNamespace(status="active", definition=optional_draft, version=2)))
    monkeypatch.setattr(executions, "production_definition", AsyncMock(return_value=SimpleNamespace(definition=graph(), version=1, release_id="release-one")))
    create = AsyncMock()
    monkeypatch.setattr(executions, "_create_execution_record", create)
    with pytest.raises(HTTPException) as error:
        await executions.execute_workflow("owned-flow", WorkflowExecuteRequest(inputs={}, execution_mode="production", release_version=1), "owned-project", AsyncMock())
    assert error.value.status_code == 422
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_executor_validates_before_any_node_callback():
    callback = AsyncMock()
    with pytest.raises(WorkflowInputError):
        await WorkflowExecutor(graph(), "owned-project").run({}, on_node_start=callback)
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_executor_rejects_out_of_range_before_any_node_callback():
    callback = AsyncMock()
    definition = graph([{
        "name": "quantity", "type": "number", "maximum": 10,
    }])
    with pytest.raises(WorkflowInputError) as error:
        await WorkflowExecutor(definition, "owned-project").run(
            {"quantity": 11}, on_node_start=callback,
        )
    assert error.value.issues[0].code == "range"
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_validates_persisted_inputs_and_cannot_use_broker_substitute(monkeypatch):
    db = AsyncMock()
    monkeypatch.setattr(tasks, "workflow_task_session", Mock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=db))))
    monkeypatch.setattr(tasks, "claim_execution", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks, "load_execution_snapshot", AsyncMock(return_value=SimpleNamespace(definition=graph(), inputs={})))
    executor = Mock()
    finished = AsyncMock(return_value=True)
    monkeypatch.setattr(tasks, "WorkflowExecutor", executor)
    monkeypatch.setattr(tasks, "finish_running_execution", finished)
    await tasks._run_workflow("execution-owned", "workflow-owned", {"order_number": "valid-broker-value"}, "project-owned")
    executor.assert_not_called()
    assert finished.await_args.args[3] == "failed"
    assert finished.await_args.args[4] is None


@pytest.mark.asyncio
async def test_worker_rechecks_persisted_length_before_running_nodes(monkeypatch):
    definition = graph([{
        "name": "order_number", "type": "string", "max_length": 8,
    }])
    db = AsyncMock()
    monkeypatch.setattr(tasks, "workflow_task_session", Mock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=db)),
    ))
    monkeypatch.setattr(tasks, "claim_execution", AsyncMock(return_value=True))
    monkeypatch.setattr(tasks, "load_execution_snapshot", AsyncMock(
        return_value=SimpleNamespace(
            definition=definition, inputs={"order_number": "PRIVATE-ORDER-123"},
        ),
    ))
    executor = Mock()
    finished = AsyncMock(return_value=True)
    monkeypatch.setattr(tasks, "WorkflowExecutor", executor)
    monkeypatch.setattr(tasks, "finish_running_execution", finished)
    await tasks._run_workflow(
        "execution-owned", "workflow-owned", {"order_number": "ORDER-1"},
        "project-owned",
    )
    executor.assert_not_called()
    assert finished.await_args.args[3] == "failed"
    assert finished.await_args.args[4] is None

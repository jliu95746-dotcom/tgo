import pytest
from app.engine.context import ExecutionContext

def test_context_get_set():
    ctx = ExecutionContext({"start.name": "TGO"})
    assert ctx.get_variable("start.name") == "TGO"
    
    ctx.set_variable("llm", "output", "Success")
    assert ctx.get_variable("llm.output") == "Success"
    assert ctx.data["llm.output"] == "Success"

def test_resolve_template():
    ctx = ExecutionContext({"user.name": "Alice", "bot.greet": "Hello"})
    
    template = "Welcome {{user.name}}! Bot says: {{bot.greet}}"
    resolved = ctx.resolve_template(template)
    assert resolved == "Welcome Alice! Bot says: Hello"
    
    # Test missing variable
    template = "Missing {{none.var}}"
    resolved = ctx.resolve_template(template)
    assert resolved == "Missing {{none.var}}"

def test_resolve_variables_recursive():
    ctx = ExecutionContext({"val": 123})
    
    input_dict = {
        "a": "Value is {{val}}",
        "b": ["List {{val}}", "Plain"],
        "c": {"nested": "{{val}}"}
    }
    
    resolved = ctx.resolve_variables(input_dict)
    assert resolved["a"] == "Value is 123"
    assert resolved["b"] == ["List 123", "Plain"]
    assert resolved["c"]["nested"] == "123"

def test_expression_context_groups_node_outputs_without_changing_flat_variables():
    context = ExecutionContext({"input.val": 10, "input.text": "hello", "limit": 5})
    names = context.get_expression_context()
    assert names["input"] == {"val": 10, "text": "hello"}
    assert names["limit"] == 5
    assert context.get_variable("input.val") == 10
    assert "input" not in context.data

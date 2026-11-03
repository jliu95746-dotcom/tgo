import pytest
from app.services.validation_service import ValidationService

def test_validate_valid_workflow():
    definition = {
        "nodes": [
            {"id": "n1", "type": "input", "data": {"label": "Input", "reference_key": "input"}},
            {"id": "n2", "type": "answer", "data": {"label": "Answer", "reference_key": "answer", "output_type": "template", "output_template": "{{input.question}}"}}
        ],
        "edges": [
            {"source": "n1", "target": "n2"}
        ]
    }
    errors = ValidationService.validate_workflow(definition)
    assert len(errors) == 0

def test_validate_missing_nodes():
    definition = {"nodes": [], "edges": []}
    errors = ValidationService.validate_workflow(definition)
    assert any("must have at least one trigger node" in e for e in errors)
    assert "Workflow must have at least one answer node" in errors

def test_validate_unreachable_node():
    definition = {
        "nodes": [
            {"id": "n1", "type": "input", "data": {"label": "Input", "reference_key": "input"}},
            {"id": "n2", "type": "llm", "data": {"label": "LLM"}},
            {"id": "n3", "type": "answer", "data": {"label": "Answer", "reference_key": "answer", "output_type": "template"}}
        ],
        "edges": [
            {"source": "n1", "target": "n3"}
        ]
    }
    errors = ValidationService.validate_workflow(definition)
    assert any("is not reachable from any trigger node" in e for e in errors)

def test_validate_circular_dependency():
    definition = {
        "nodes": [
            {"id": "n1", "type": "input", "data": {"label": "Input", "reference_key": "input"}},
            {"id": "n2", "type": "llm", "data": {"label": "LLM"}},
            {"id": "n3", "type": "answer", "data": {"label": "Answer", "reference_key": "answer", "output_type": "template"}}
        ],
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n2"} # Self loop
        ]
    }
    errors = ValidationService.validate_workflow(definition)
    assert "Workflow contains circular dependencies" in errors


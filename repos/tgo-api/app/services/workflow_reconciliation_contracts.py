"""Validate review responses without leaking malformed payloads."""

from typing import TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.schemas.workflow_reconciliation import WorkflowReconciliationSummary

Contract = TypeVar("Contract", bound=BaseModel)


def reconciliation_contract(value: object, model: type[Contract]) -> Contract:
    try:
        return model.model_validate(value)
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(502, "工作流核验响应无法校验") from None


def reconciliation_list(value: object) -> list[WorkflowReconciliationSummary]:
    if not isinstance(value, list) or len(value) > 100:
        raise HTTPException(502, "工作流核验列表响应无效")
    return [
        reconciliation_contract(row, WorkflowReconciliationSummary)
        for row in value
    ]

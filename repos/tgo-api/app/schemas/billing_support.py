"""Customer invoice requests and reviewed operator actions."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    order_id: UUID
    title: str = Field(min_length=2, max_length=200)
    tax_number: str = Field(min_length=8, max_length=40, pattern=r"^[A-Za-z0-9]+$")
    email: EmailStr = Field(max_length=254)


class InvoiceResponse(InvoiceCreate):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    status: str
    invoice_number: str | None
    created_at: datetime


class InvoiceProcess(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Literal["issued", "rejected"]
    invoice_number: str | None = Field(default=None, min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)


class QuotaBatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    amount: int
    remaining: int
    expires_at: datetime
    created_at: datetime
    order_id: UUID | None


class QuotaReservationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID
    round_key: str
    batch_id: UUID
    status: str
    attempt: int
    created_at: datetime
    settled_at: datetime | None


class QuotaResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["settle", "release"]
    reason: str = Field(min_length=5, max_length=500)


class CompanyStateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["suspend", "restore"]
    reason: str = Field(min_length=5, max_length=500)


class ReconciliationIssue(BaseModel):
    code: str
    order_number: str
    project_id: UUID | None


class ReconciliationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    bill_date: date
    status: str
    entry_count: int
    issue_count: int
    issues: list[ReconciliationIssue]
    completed_at: datetime

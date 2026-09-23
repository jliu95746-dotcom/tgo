"""Platform-only company overview and reasoned, idempotent quota adjustments."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class OperationsCompany(BaseModel):
    id: UUID
    name: str
    status: str
    plan_name: str | None
    expires_at: datetime | None
    seats: int | None
    used: int
    reserved: int
    ai_remaining: int
    order_exceptions: int


class CreditAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    delta: int = Field(ge=-100000000, le=100000000, strict=True)
    expires_at: datetime | None = None
    reason: str = Field(min_length=5, max_length=500)

    @model_validator(mode="after")
    def validate_adjustment(self) -> "CreditAdjustment":
        if not self.delta:
            raise ValueError("调整次数不能为零")
        if self.delta > 0 and (
            self.expires_at is None or self.expires_at.tzinfo is None
        ):
            raise ValueError("增加次数必须指定含时区的有效期")
        if self.delta < 0 and self.expires_at is not None:
            raise ValueError("扣减次数不修改原有效期")
        return self

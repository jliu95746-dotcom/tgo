from pydantic import BaseModel, Field
from typing import List, TypeVar, Generic

T = TypeVar("T")


class MessageResponse(BaseModel):
    message: str = Field(..., description="Message content")


class HealthCheckResponse(BaseModel):
    status: str = Field(..., description="Health status of the service")


class PaginationInfo(BaseModel):
    total: int = Field(..., description="Total number of items")
    limit: int = Field(..., description="Items per page")
    offset: int = Field(..., description="Offset (skip)")
    has_next: bool = Field(..., description="Whether there are more items")
    has_prev: bool = Field(..., description="Whether there are previous items")


class PaginatedResponse(BaseModel, Generic[T]):
    data: List[T] = Field(..., description="List of items for current page")
    pagination: PaginationInfo = Field(..., description="Pagination metadata")

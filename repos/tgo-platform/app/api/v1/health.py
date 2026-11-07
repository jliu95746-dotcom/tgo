from __future__ import annotations
from fastapi import APIRouter
from app.api.schemas import ErrorResponse

router = APIRouter()


@router.get("/health", responses={500: {"model": ErrorResponse}})
async def health() -> dict:
    return {"status": "ok"}

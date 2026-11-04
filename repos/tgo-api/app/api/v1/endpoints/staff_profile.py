"""Self-service profile routes; public endpoints serve avatar bytes only."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.models import Staff
from app.schemas.staff import StaffProfileUpdate, StaffResponse
from app.services.staff_profile_service import avatar_path, save_avatar, update_profile

router = APIRouter()


@router.patch("/me", response_model=StaffResponse)
async def edit_my_profile(
    data: StaffProfileUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> StaffResponse:
    return StaffResponse.model_validate(update_profile(db, current_user, data))


@router.post("/me/avatar", response_model=StaffResponse)
async def upload_my_avatar(
    file: UploadFile = File(...),
    nickname: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> StaffResponse:
    try:
        profile = (
            StaffProfileUpdate(nickname=nickname) if nickname is not None else None
        )
    except ValidationError as exc:
        raise HTTPException(422, "昵称不能为空，且不能超过 100 个字符") from exc
    staff = await save_avatar(db, current_user, file, profile)
    return StaffResponse.model_validate(staff)


@router.get("/{staff_id}/avatar/{filename}", response_class=FileResponse)
def get_staff_avatar(
    staff_id: UUID,
    filename: str,
    db: Session = Depends(get_db),
) -> FileResponse:
    staff = (
        db.query(Staff).filter(Staff.id == staff_id, Staff.deleted_at.is_(None)).first()
    )
    if staff is None:
        raise HTTPException(404, "头像不存在")
    path = avatar_path(staff, filename)
    if not path.is_file():
        raise HTTPException(404, "头像不存在")
    return FileResponse(
        path,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )

"""Staff endpoints."""

from datetime import datetime, timedelta
from typing import Dict, List
from uuid import UUID

from fastapi import (
    APIRouter, Depends, Form, HTTPException, Request, Response, status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import (
    authenticate_user,
    create_access_token,
    get_current_active_user,
    get_password_hash,
    require_permission,
    require_admin,
)
from app.models import Staff
from app.models.visitor_assignment_rule import VisitorAssignmentRule
from app.services.queue_trigger_service import trigger_queue_for_staff
from app.services.wukongim_client import wukongim_client
from app.utils.const import CHANNEL_TYPE_PROJECT_STAFF
from app.utils.encoding import build_project_staff_channel_id
from app.schemas import (
    StaffCreate,
    StaffListParams,
    StaffListResponse,
    StaffLogin,
    StaffLoginResponse,
    StaffResponse,
    StaffUpdate,
)
from app.schemas.wukongim import (
    WuKongIMIntegrationStatus,
    WuKongIMOnlineStatusRequest,
    WuKongIMOnlineStatusResponse,
)
from app.services.wukongim_client import wukongim_client
from app.services.transfer_service import is_within_service_hours
from app.services.project_registration import ensure_project_staff_channel
from app.api.common_responses import AUTH_RESPONSES, CRUD_RESPONSES, LIST_RESPONSES
from app.services.staff_browser_session import (
    IDLE_SECONDS,
    end_browser_session,
    refresh_browser_session,
    start_browser_session,
)

logger = get_logger("endpoints.staff")
router = APIRouter()


def _build_staff_response(staff: Staff, is_working: bool = None) -> StaffResponse:
    """Build StaffResponse with optional is_working field."""
    data = {
        "id": staff.id,
        "project_id": staff.project_id,
        "username": staff.username,
        "name": staff.name,
        "nickname": staff.nickname,
        "avatar_url": staff.avatar_url,
        "description": staff.description,
        "role": staff.role,
        "status": staff.status,
        "is_active": staff.is_active,
        "service_paused": staff.service_paused,
        "account_enabled": staff.account_enabled,
        "email_verified_at": staff.email_verified_at,
        "is_working": is_working,
        "created_at": staff.created_at,
        "updated_at": staff.updated_at,
        "deleted_at": staff.deleted_at,
    }
    return StaffResponse(**data)


@router.post(
    "/login",
    response_model=StaffLoginResponse,
    responses=AUTH_RESPONSES
)
async def login_staff(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    remember_session: bool = Form(False),
) -> StaffLoginResponse:
    """
    Staff login.

    Authenticate staff member and return JWT access token.
    Also registers/synchronizes user with WuKongIM for instant messaging.
    """
    logger.info(f"Staff login attempt for username: {form_data.username}")

    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        logger.warning(f"Failed login attempt for username: {form_data.username}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if remember_session:
        await start_browser_session(request, response, user)

    access_seconds = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    if remember_session:
        access_seconds = min(access_seconds, IDLE_SECONDS)
    access_token_expires = timedelta(seconds=access_seconds)
    access_token = create_access_token(
        subject=user.username,
        project_id=user.project_id,
        role=user.role,
        expires_delta=access_token_expires,
        token_version=user.token_version,
    )

    # Register/synchronize user with WuKongIM for instant messaging
    # Use staff ID with "-staff" suffix to ensure unique identification
    staff_uid = f"{user.id}-staff"
    try:
        await wukongim_client.register_or_login_user(
            uid=staff_uid,
            token=access_token,  # Use the JWT token as WuKongIM token
        )
        await ensure_project_staff_channel(db, user)
        logger.info(f"Successfully synchronized staff {user.username} (UID: {staff_uid}) with WuKongIM")
    except Exception as e:
        # Log the error but don't fail the login process
        logger.error(
            f"Failed to synchronize user {user.username} with WuKongIM: {e}",
            extra={
                "username": user.username,
                "error": str(e),
                "wukongim_enabled": settings.WUKONGIM_ENABLED,
            }
        )
        # WuKongIM sync failure should not prevent login
        # The user can still use the main application features

    logger.info(f"Successful login for user: {user.username}")

    return StaffLoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=access_seconds,
        staff=StaffResponse.model_validate(user)
    )


@router.post("/session/refresh", response_model=StaffLoginResponse)
async def refresh_staff_session(
    request: Request,
    response: Response,
    active: bool = False,
    db: Session = Depends(get_db),
) -> StaffLoginResponse:
    return await refresh_browser_session(request, response, db, active)


@router.post("/session/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_staff_session(request: Request, response: Response) -> None:
    await end_browser_session(request, response)


@router.get(
    "",
    response_model=StaffListResponse,
    responses=LIST_RESPONSES
)
async def list_staff(
    params: StaffListParams = Depends(),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:list")),
) -> StaffListResponse:
    """
    List staff members.
    
    Retrieve a paginated list of staff members with optional filtering.
    Requires staff:list permission.
    
    Returns is_working field indicating if current time is within service hours.
    """
    logger.info(f"User {current_user.username} listing staff members")
    
    # Get assignment rule for is_working calculation
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    # Build query
    query = db.query(Staff).filter(
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    )
    
    # Apply filters
    if params.role:
        query = query.filter(Staff.role == params.role)
    if params.status:
        query = query.filter(Staff.status == params.status)
    
    # Get total count
    total = query.count()
    
    # Apply pagination
    staff_members = query.offset(params.offset).limit(params.limit).all()
    
    # Convert to response models with is_working
    staff_responses = [_build_staff_response(staff, is_working) for staff in staff_members]
    
    return StaffListResponse(
        data=staff_responses,
        pagination={
            "total": total,
            "limit": params.limit,
            "offset": params.offset,
            "has_next": params.offset + params.limit < total,
            "has_prev": params.offset > 0,
        }
    )


@router.post("", response_model=StaffResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    staff_data: StaffCreate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:create")),
) -> StaffResponse:
    """
    Create staff member.
    
    Create a new staff member. Requires staff:create permission (admin only by default).
    Only staff members with 'user' role can be created through this endpoint.
    """
    logger.info(f"User {current_user.username} creating staff: {staff_data.username}")
    
    # Only allow creating staff with 'user' role
    if settings.SAAS_BILLING_ENABLED:
        raise HTTPException(409, "请通过企业成员邀请添加客服并验证邮箱")
    if staff_data.role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only create staff members with 'user' role"
        )
    
    # Check if username already exists
    existing_staff = db.query(Staff).filter(
        Staff.username == staff_data.username,
        Staff.deleted_at.is_(None)
    ).first()
    
    if existing_staff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    # Hash password
    password_hash = get_password_hash(staff_data.password)
    
    # Create staff member
    staff = Staff(
        project_id=current_user.project_id,
        username=staff_data.username,
        password_hash=password_hash,
        name=staff_data.name,
        nickname=staff_data.nickname,
        avatar_url=staff_data.avatar_url,
        description=staff_data.description,
        role=staff_data.role,
        status=staff_data.status,
    )
    
    db.add(staff)
    db.flush()  # Get staff.id before WuKongIM call
    
    # Add staff to project staff channel
    try:
        channel_id = build_project_staff_channel_id(current_user.project_id)
        staff_uid = f"{staff.id}-staff"
        await wukongim_client.add_channel_subscribers(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_PROJECT_STAFF,
            subscribers=[staff_uid],
        )
    except Exception as e:
        logger.error(f"Failed to add staff {staff.id} to project channel: {e}")
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add staff to project channel"
        )
    
    db.commit()
    db.refresh(staff)
    
    logger.info(f"Created staff {staff.id} with username: {staff.username}")
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(staff, is_working)


@router.get("/me", response_model=StaffResponse)
async def get_current_staff(
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> StaffResponse:
    """
    Get current staff member info.
    
    Returns the current authenticated staff member's information.
    Includes is_working field based on VisitorAssignmentRule service hours.
    """
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(current_user, is_working)


@router.put(
    "/me/service-paused",
    response_model=StaffResponse,
    summary="Toggle Service Paused Status",
    description="Toggle current staff member's service paused status."
)
async def toggle_my_service_paused(
    paused: bool,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> StaffResponse:
    """
    Toggle current staff's service paused status.
    
    When paused is True, the staff member will not be assigned new visitors.
    """
    logger.info(f"Staff {current_user.username} setting service_paused to {paused}")
    
    current_user.service_paused = paused
    current_user.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(current_user)
    
    logger.info(f"Staff {current_user.username} service_paused is now {current_user.service_paused}")
    
    # Trigger queue processing if staff resumed service
    if not paused:
        await trigger_queue_for_staff(current_user.id, current_user.project_id)
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(current_user, is_working)


@router.put(
    "/me/is-active",
    response_model=StaffResponse,
    summary="Toggle Service Active Status",
    description="Toggle current staff member's service active status (start/stop service)."
)
async def toggle_my_is_active(
    active: bool,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
) -> StaffResponse:
    """
    Toggle current staff's service active status.
    
    When active is False, the staff member is stopped from service (long-term, e.g., off-duty).
    When active is True, the staff member starts service.
    """
    logger.info(f"Staff {current_user.username} setting is_active to {active}")
    
    current_user.is_active = active
    current_user.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(current_user)
    
    logger.info(f"Staff {current_user.username} is_active is now {current_user.is_active}")
    
    # Trigger queue processing if staff activated service
    if active:
        await trigger_queue_for_staff(current_user.id, current_user.project_id)
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(current_user, is_working)


@router.get("/{staff_id}", response_model=StaffResponse)
async def get_staff(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:read")),
) -> StaffResponse:
    """Get staff member details. Requires staff:read permission."""
    logger.info(f"User {current_user.username} getting staff: {staff_id}")
    
    staff = db.query(Staff).filter(
        Staff.id == staff_id,
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).first()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff member not found"
        )
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(staff, is_working)


@router.patch("/{staff_id}", response_model=StaffResponse)
async def update_staff(
    staff_id: UUID,
    staff_data: StaffUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:update")),
) -> StaffResponse:
    """
    Update staff member.
    
    Update staff member information. Requires staff:update permission.
    """
    logger.info(f"User {current_user.username} updating staff: {staff_id}")
    
    staff = db.query(Staff).filter(
        Staff.id == staff_id,
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).first()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff member not found"
        )
    
    # Update fields
    if settings.SAAS_BILLING_ENABLED:
        from app.services.company_membership import lock_company, protect_last_admin, validate_human_role_change
        lock_company(db, current_user.project_id, current_user)
        db.refresh(staff)
        if staff_data.role is not None:
            validate_human_role_change(staff, staff_data.role)
        if staff_data.role is not None and staff_data.role != "admin":
            protect_last_admin(db, staff)
    update_data = staff_data.model_dump(exclude_unset=True)
    
    # Handle password update
    if "password" in update_data:
        update_data["password_hash"] = get_password_hash(update_data.pop("password"))
        staff.token_version += 1
    
    for field, value in update_data.items():
        setattr(staff, field, value)
    
    staff.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(staff)
    
    logger.info(f"Updated staff {staff.id}")
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(staff, is_working)


@router.put(
    "/{staff_id}/service-paused",
    response_model=StaffResponse,
    summary="Set Staff Service Paused Status",
    description="Set a staff member's service paused status. Requires staff:update permission."
)
async def set_staff_service_paused(
    staff_id: UUID,
    paused: bool,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:update")),
) -> StaffResponse:
    """
    Set staff member's service paused status.
    
    When paused is True, the staff member will not be assigned new visitors.
    Requires staff:update permission.
    """
    logger.info(f"User {current_user.username} setting service_paused to {paused} for staff {staff_id}")
    
    staff = db.query(Staff).filter(
        Staff.id == staff_id,
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).first()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff member not found"
        )
    
    staff.service_paused = paused
    staff.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(staff)
    
    logger.info(f"Staff {staff.username} service_paused is now {staff.service_paused}")
    
    # Trigger queue processing if staff resumed service
    if not paused:
        await trigger_queue_for_staff(staff.id, staff.project_id)
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(staff, is_working)


@router.put(
    "/{staff_id}/is-active",
    response_model=StaffResponse,
    summary="Set Staff Service Active Status",
    description="Set a staff member's service active status (start/stop service). Requires staff:update permission."
)
async def set_staff_is_active(
    staff_id: UUID,
    active: bool,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:update")),
) -> StaffResponse:
    """
    Set staff member's service active status.
    
    When active is False, the staff member is stopped from service (long-term, e.g., off-duty).
    When active is True, the staff member starts service.
    Requires staff:update permission.
    """
    logger.info(f"User {current_user.username} setting is_active to {active} for staff {staff_id}")
    
    staff = db.query(Staff).filter(
        Staff.id == staff_id,
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).first()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff member not found"
        )
    
    staff.is_active = active
    staff.updated_at = datetime.utcnow()
    
    db.commit()
    db.refresh(staff)
    
    logger.info(f"Staff {staff.username} is_active is now {staff.is_active}")
    
    # Trigger queue processing if staff activated service
    if active:
        await trigger_queue_for_staff(staff.id, staff.project_id)
    
    # Calculate is_working
    assignment_rule = db.query(VisitorAssignmentRule).filter(
        VisitorAssignmentRule.project_id == current_user.project_id
    ).first()
    is_working = is_within_service_hours(assignment_rule)
    
    return _build_staff_response(staff, is_working)


@router.delete("/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    staff_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("staff:delete")),
) -> None:
    """
    Delete staff member (soft delete).
    
    Soft delete a staff member. Requires staff:delete permission.
    """
    logger.info(f"User {current_user.username} deleting staff: {staff_id}")
    
    staff = db.query(Staff).filter(
        Staff.id == staff_id,
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).first()
    
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Staff member not found"
        )
    
    # Prevent self-deletion
    if settings.SAAS_BILLING_ENABLED:
        from app.models import VisitorSession
        from app.services.company_membership import lock_company, protect_last_admin
        lock_company(db, current_user.project_id, current_user)
        db.refresh(staff)
        protect_last_admin(db, staff)
        if db.query(VisitorSession.id).filter(
            VisitorSession.project_id == staff.project_id,
            VisitorSession.staff_id == staff.id,
            VisitorSession.status == "open",
        ).first():
            raise HTTPException(409, "请先转交或退回该成员的未结束会话")
    if staff.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself"
        )
    
    # Remove staff from project staff channel first
    try:
        channel_id = build_project_staff_channel_id(current_user.project_id)
        staff_uid = f"{staff.id}-staff"
        await wukongim_client.remove_channel_subscribers(
            channel_id=channel_id,
            channel_type=CHANNEL_TYPE_PROJECT_STAFF,
            subscribers=[staff_uid],
        )
    except Exception as e:
        logger.error(f"Failed to remove staff {staff.id} from project channel: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to remove staff from project channel"
        )
    
    # Soft delete
    staff.deleted_at = datetime.utcnow()
    staff.updated_at = datetime.utcnow()
    
    db.commit()
    
    logger.info(f"Deleted staff {staff.id}")

    return None


@router.get(
    "/wukongim/status",
    response_model=WuKongIMIntegrationStatus,
    summary="Get WuKongIM Integration Status",
    description="Get the current status of WuKongIM integration including configuration and health."
)
async def get_wukongim_status(
    current_user: Staff = Depends(get_current_active_user),
) -> WuKongIMIntegrationStatus:
    """Get WuKongIM integration status."""
    logger.info(f"User {current_user.username} checking WuKongIM status")

    return WuKongIMIntegrationStatus(
        enabled=settings.WUKONGIM_ENABLED,
        service_url=settings.WUKONGIM_SERVICE_URL,
        last_sync=None,  # Could be enhanced to track last sync time
        error_count=0,   # Could be enhanced to track errors
        last_error=None  # Could be enhanced to track last error
    )


@router.post(
    "/wukongim/online-status",
    response_model=WuKongIMOnlineStatusResponse,
    summary="Check Staff Online Status",
    description="Check which staff members are currently online in WuKongIM."
)
async def check_staff_online_status(
    request: WuKongIMOnlineStatusRequest,
    current_user: Staff = Depends(get_current_active_user),
    db: Session = Depends(get_db),
) -> WuKongIMOnlineStatusResponse:
    """Check online status of staff members in WuKongIM."""
    logger.info(
        f"User {current_user.username} checking online status for {len(request.uids)} users"
    )

    # Validate that the requested UIDs are valid staff usernames in the same project
    valid_staff = db.query(Staff).filter(
        Staff.username.in_(request.uids),
        Staff.project_id == current_user.project_id,
        Staff.deleted_at.is_(None)
    ).all()

    # Convert staff records to WuKongIM UIDs (staff_id + "-staff")
    staff_uid_mapping = {staff.username: f"{staff.id}-staff" for staff in valid_staff}
    valid_usernames = list(staff_uid_mapping.keys())
    wukongim_uids = list(staff_uid_mapping.values())

    if len(valid_usernames) != len(request.uids):
        invalid_uids = set(request.uids) - set(valid_usernames)
        logger.warning(
            f"Invalid UIDs requested: {invalid_uids}",
            extra={"invalid_uids": list(invalid_uids)}
        )

    try:
        online_uids = await wukongim_client.check_user_online_status(wukongim_uids)

        # Convert WuKongIM UIDs back to staff usernames for response
        reverse_mapping = {wukongim_uid: username for username, wukongim_uid in staff_uid_mapping.items()}
        online_usernames = [reverse_mapping[wukongim_uid] for wukongim_uid in online_uids if wukongim_uid in reverse_mapping]
        logger.info(f"Found {len(online_usernames)} online staff members")

        return WuKongIMOnlineStatusResponse(online_uids=online_usernames)

    except Exception as e:
        logger.error(f"Failed to check online status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check online status"
        )

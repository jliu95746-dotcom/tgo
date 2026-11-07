"""Visitor endpoints."""

import re
import uuid
from urllib.parse import urlparse
from datetime import datetime, timedelta
from typing import Optional, List
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
    Query,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.config import settings
from app.core.security import verify_token

from app.services.wukongim_client import wukongim_client
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.services.im_access import issue_visitor_im_token
from app.schemas.visitor_access import VisitorAccessInput
from app.services.visitor_access import (
    registration_access,
    require_access,
    denied as visitor_access_denied,
)
from app.services.wechat_local_bridge import (
    authorize_bridge_visitor,
    require_scoped_target,
)
from app.utils.wechat_local import is_filehelper_identity, is_scoped_identity
from app.services.reply_history import reconcile_reply_history
from app.core.exceptions import TGOAPIException
from app.services.visitor_data_access import (
    require_visitor_data_access,
    authorize_visitor_data_channel,
    reauthorize_visitor_data,
)
from app.services.visitor_history import require_owned_history_batch
from app.api.current_access_route import CurrentAccessRoute
from app.schemas.visitor_public_profile import VisitorPublicProfile
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.profile_access import (
    require_profile_access,
    require_profile_visitor,
    profile_guard,
)
from app.services.staff_record_access import (
    require_staff_record_access,
    staff_record_guard,
)
from app.services.visitor_mutation_access import (
    begin_mutation,
    commit_change,
    create_customer,
    VisitorMutationScope,
)
from app.services.visitor_registration_output import (
    RegistrationOrigin,
    registration_output,
)

from app.schemas.employee_style import EmployeeStyle
from app.services.employee_style import resolve_employee_style
from app.services.visitor_notifications import notify_visitor_profile_updated
from app.services.platform_message_client import prepare_native_mode
from app.utils.intent import localize_visitor_response_intent
from app.models.platform import PlatformType
from app.utils.wechat_local import is_filehelper_identity
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE, MEMBER_TYPE_VISITOR
from app.utils.request import get_client_ip, get_client_language
from app.services.geoip_service import geoip_service
import app.services.visitor_service as visitor_service
from app.utils.encoding import (
    build_visitor_channel_id,
    parse_visitor_channel_id,
)


from app.core.database import get_db
from app.core.logging import get_logger
from app.core.security import (
    get_current_active_user,
    get_user_language,
    UserLanguage,
    require_permission,
)
from app.models import (
    Platform,
    PlatformType,
    Staff,
    Visitor,
    VisitorActivity,
    VisitorSystemInfo,
    VisitorTag,
    ChannelMember,
    VisitorWaitingQueue,
    WaitingStatus,
    AssignmentSource,
    VisitorServiceStatus,
)
from app.schemas import (
    VisitorAIInsightResponse,
    VisitorAIProfileResponse,
    VisitorActivityResponse,
    VisitorAttributesUpdate,
    VisitorAvatarUploadResponse,
    VisitorCreate,
    VisitorListResponse,
    VisitorResponse,
    VisitorBasicResponse,
    VisitorSystemInfoRequest,
    VisitorSystemInfoResponse,
    VisitorUpdate,
    TagResponse,
    VisitorActivityCreateRequest,
    VisitorActivityCreateResponse,
    AcceptVisitorResponse,
    VisitorRegisterRequest,
    VisitorRegisterResponse,
    VisitorMessageSyncRequest,
    VisitorServiceModeUpdate,
)
from app.schemas.visitor import (
    set_visitor_display_nickname,
    set_visitor_list_display_nickname,
    populate_visitor_ai_settings,
)
from app.schemas.tag import set_tag_list_display_name

from app.schemas.wukongim import WuKongIMChannelMessageSyncResponse
from app.schemas.base import PaginationMetadata
from app.services.transfer_service import transfer_to_staff


logger = get_logger("endpoints.visitors")
from app.services.staff_conversation_scope import (
    enforce_visitor_detail_scope,
    owned_visitor_ids,
    restricted_staff,
)

router = APIRouter(
    route_class=CurrentAccessRoute,
    dependencies=[Depends(enforce_visitor_detail_scope)],
)


async def _visitor_mutation_response(
    request: Request,
    db: Session,
    visitor: Visitor,
    scope: VisitorMutationScope,
    user_language: UserLanguage,
) -> VisitorResponse:
    guard = scope.guard()
    request.state.operation_guard = guard
    guard.check()
    if settings.CUSTOMER_TURNS_ENABLED and visitor.service_mode != "auto":
        from app.services.customer_turn_ingress import cancel_customer_turns

        await cancel_customer_turns(db, visitor)
        guard.check()
    await notify_visitor_profile_updated(db, visitor)
    guard.check()
    response = VisitorResponse.model_validate(visitor)
    populate_visitor_ai_settings(response, visitor.platform)
    localize_visitor_response_intent(
        response, request.headers.get("Accept-Language")
    )
    set_visitor_display_nickname(response, user_language)
    return response


@router.get("", response_model=VisitorListResponse)
async def list_visitors(
    request: Request,
    platform_id: Optional[UUID] = Query(
        None, description="Filter visitors by platform ID"
    ),
    is_online: Optional[bool] = Query(
        None, description="Filter visitors by online status"
    ),
    recent_online_minutes: Optional[int] = Query(
        None,
        description="Filter visitors who were online within this many minutes (including currently online)",
    ),
    service_status: Optional[List[str]] = Query(
        None,
        description="Filter visitors by service status (e.g., 'new', 'queued', 'active', 'closed')",
    ),
    tag_ids: Optional[List[str]] = Query(
        None, description="Filter visitors by tag IDs (OR relationship)"
    ),
    search: Optional[str] = Query(
        None, description="Search visitors by name, nickname, geo, ip, etc."
    ),
    sort_by: str = Query(
        "created_at",
        description="Sort field: 'created_at', 'last_visit_time', or 'last_offline_time'",
    ),
    sort_order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    offset: int = Query(0, ge=0, description="Number of visitors to skip"),
    limit: int = Query(
        20, ge=1, le=100, description="Number of visitors to return"
    ),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:list")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorListResponse:
    """
    List visitors.

    Retrieve a paginated list of visitors with optional filtering by platform,
    online status, tags, and search query. Requires visitors:list permission.
    """
    logger.info(
        f"User {current_user.username} listing visitors (tag_ids={tag_ids}, recent_online_minutes={recent_online_minutes}, service_status={service_status})"
    )

    access = require_staff_record_access(
        db, credentials, current_user, "visitors:list"
    )

    # Build query
    query = (
        db.query(Visitor)
        .options(
            joinedload(Visitor.platform),
            selectinload(Visitor.visitor_tags).selectinload(VisitorTag.tag),
        )
        .filter(
            Visitor.project_id == current_user.project_id,
            Visitor.deleted_at.is_(None),
        )
    )

    # Apply filters
    if restricted_staff(current_user):
        query = query.filter(Visitor.id.in_(owned_visitor_ids(current_user)))
    if platform_id:
        query = query.filter(Visitor.platform_id == platform_id)
    if is_online is not None:
        query = query.filter(Visitor.is_online == is_online)

    # Filter by service status
    if service_status:
        query = query.filter(Visitor.service_status.in_(service_status))

    # Filter by recent online status (online or offline within X minutes)
    if recent_online_minutes is not None:
        cutoff = datetime.utcnow() - timedelta(minutes=recent_online_minutes)
        query = query.filter(
            (Visitor.is_online == True) | (Visitor.last_offline_time >= cutoff)
        )

    # Filter by tags
    if tag_ids:
        # Use subquery for tag filtering to ensure count() works correctly with distinct visitors
        tag_visitor_ids = (
            db.query(VisitorTag.visitor_id)
            .filter(
                VisitorTag.tag_id.in_(tag_ids), VisitorTag.deleted_at.is_(None)
            )
            .subquery()
        )
        query = query.filter(
            Visitor.id.in_(select(tag_visitor_ids.c.visitor_id))
        )

    if search:
        search_term = f"%{search}%"
        query = query.filter(
            (Visitor.name.ilike(search_term))
            | (Visitor.nickname.ilike(search_term))
            | (Visitor.nickname_zh.ilike(search_term))
            | (Visitor.phone_number.ilike(search_term))
            | (Visitor.email.ilike(search_term))
            | (Visitor.company.ilike(search_term))
            | (Visitor.geo_country.ilike(search_term))
            | (Visitor.geo_region.ilike(search_term))
            | (Visitor.geo_city.ilike(search_term))
            | (Visitor.geo_isp.ilike(search_term))
            | (Visitor.ip_address.ilike(search_term))
            | (Visitor.note.ilike(search_term))
        )

    # Get total count
    total = query.count()

    # Apply sorting
    if sort_by == "last_offline_time":
        if sort_order == "desc":
            # 1. Online visitors first (is_online=True -> 0, False -> 1, so asc puts True first)
            # 2. If online, sort by created_at desc
            # 3. If offline, sort by last_offline_time desc (most recently offline first)
            # 4. NULL last_offline_time values go last
            query = query.order_by(
                case((Visitor.is_online == True, 0), else_=1).asc(),
                case(
                    (Visitor.is_online == True, Visitor.created_at),
                    else_=Visitor.last_offline_time,
                )
                .desc()
                .nulls_last(),
            )
        else:
            # Opposite of desc: offline first, then by oldest offline time
            query = query.order_by(
                case((Visitor.is_online == True, 1), else_=0).asc(),
                case(
                    (Visitor.is_online == True, Visitor.created_at),
                    else_=Visitor.last_offline_time,
                )
                .asc()
                .nulls_first(),
            )
    else:
        if sort_by == "last_visit_time":
            sort_attr = Visitor.last_visit_time
        else:
            sort_attr = Visitor.created_at

        if sort_order == "asc":
            query = query.order_by(sort_attr.asc())
        else:
            query = query.order_by(sort_attr.desc())

    # Apply pagination
    visitors = query.offset(offset).limit(limit).all()

    guard = staff_record_guard(
        access, tuple(visitor.id for visitor in visitors), "visitors:list"
    )
    request.state.operation_guard = guard
    guard.check()

    # Convert to response models
    accept_language = request.headers.get("Accept-Language")
    visitor_responses = []
    for visitor in visitors:
        vr = VisitorResponse.model_validate(visitor)
        populate_visitor_ai_settings(vr, visitor.platform)
        visitor_responses.append(vr)

    for vr in visitor_responses:
        localize_visitor_response_intent(vr, accept_language)
        if vr.tags:
            set_tag_list_display_name(vr.tags, user_language)
    set_visitor_list_display_nickname(visitor_responses, user_language)

    return VisitorListResponse(
        data=visitor_responses,
        pagination=PaginationMetadata(
            total=total,
            limit=limit,
            offset=offset,
            has_next=offset + limit < total,
            has_prev=offset > 0,
        ),
    )


@router.post(
    "", response_model=VisitorResponse, status_code=status.HTTP_201_CREATED
)
async def create_visitor(
    request: Request,
    visitor_data: VisitorCreate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:create")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    """
    Create visitor.

    Create a new visitor record. Requires either platform_id or platform_type.
    If platform_type is provided, uses the default platform of that type.
    Requires visitors:create permission.
    """
    logger.info(
        f"User {current_user.username} creating visitor: {visitor_data.platform_open_id}"
    )

    access = require_staff_record_access(
        db, credentials, current_user, "visitors:create"
    )

    # Determine platform_id
    platform_id = visitor_data.platform_id
    if not platform_id and visitor_data.platform_type:
        # Find default platform of the specified type
        platform = (
            db.query(Platform)
            .filter(
                Platform.project_id == current_user.project_id,
                Platform.type == visitor_data.platform_type,
                Platform.is_active == True,
                Platform.deleted_at.is_(None),
            )
            .first()
        )

        if not platform:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"No active platform found for type: {visitor_data.platform_type}",
            )
        platform_id = platform.id

    if not platform_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either platform_id or platform_type must be provided",
        )

    # Check if visitor already exists for this platform
    existing_visitor = (
        db.query(Visitor)
        .filter(
            Visitor.project_id == current_user.project_id,
            Visitor.platform_id == platform_id,
            Visitor.platform_open_id == visitor_data.platform_open_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )

    if existing_visitor:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Visitor already exists for this platform",
        )

    # Get real IP and language (from request body or headers)
    real_ip = get_client_ip(request, visitor_data.ip_address)
    real_language = get_client_language(request, visitor_data.language)

    # Lookup geolocation from IP address
    geo_location = geoip_service.lookup(real_ip)

    # Create visitor
    visitor = Visitor(
        project_id=current_user.project_id,
        platform_id=platform_id,
        platform_open_id=visitor_data.platform_open_id,
        name=visitor_data.name,
        nickname=visitor_data.nickname,
        nickname_zh=visitor_data.nickname_zh,
        avatar_url=visitor_data.avatar_url,
        phone_number=visitor_data.phone_number,
        email=visitor_data.email,
        company=visitor_data.company,
        job_title=visitor_data.job_title,
        source=visitor_data.source,
        note=visitor_data.note,
        custom_attributes=visitor_data.custom_attributes or {},
        timezone=visitor_data.timezone,
        language=real_language,
        ip_address=real_ip,
        geo_country=geo_location.country,
        geo_country_code=geo_location.country_code,
        geo_region=geo_location.region,
        geo_city=geo_location.city,
        geo_isp=geo_location.isp,
    )

    visitor, output_scope = create_customer(db, access, visitor)

    logger.info(f"Created visitor {visitor.id} for platform {platform_id}")

    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.post(
    "/register",
    response_model=VisitorRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_visitor(
    request: Request,
    req: VisitorRegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
    user_language: UserLanguage = Depends(get_user_language),
) -> VisitorRegisterResponse:
    """Register a visitor using a Platform API key (visitor-facing use).

    - Authenticates via platform_api_key
    - Creates or returns an existing visitor bound to the platform/project
    - Returns channel info for messaging integration
    """
    # 1) Validate platform_api_key
    platform = (
        db.query(Platform)
        .filter(
            Platform.api_key == req.platform_api_key,
            Platform.is_active == True,
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid platform API key",
        )

    local_wechat = platform.type == PlatformType.WECHAT_LOCAL.value
    if local_wechat and (
        req.customer_sessions
        or not (
            is_filehelper_identity(req.platform_open_id)
            or is_scoped_identity(req.platform_open_id)
        )
    ):
        raise visitor_access_denied()
    if platform.type != "website" and not local_wechat:
        raise HTTPException(status_code=403, detail="此渠道的访客由已验证的服务端消息接入")

    from pydantic import SecretStr

    origin = RegistrationOrigin(
        platform.project_id,
        platform.id,
        SecretStr(req.platform_api_key),
        platform.type,
    )
    origin.check(db)
    if platform.type == PlatformType.WECHAT_LOCAL.value:
        if is_filehelper_identity(req.platform_open_id):
            nickname = "文件传输助手（个人微信）"
        else:
            target = await require_scoped_target(
                platform, req.platform_open_id or ""
            )
            nickname = (
                req.nickname or ("微信群聊" if target.is_group else "微信联系人")
            )[:80]
        req = VisitorRegisterRequest(
            platform_api_key=req.platform_api_key,
            platform_open_id=req.platform_open_id,
            nickname=nickname,
            visitor_access_token=req.visitor_access_token,
            im_token=req.im_token,
            visitor_id=req.visitor_id,
            language="zh-CN",
            source="wechat-local-bridge",
        )

    # 2) Find existing or create new visitor
    normalized_open_id = (req.platform_open_id or "").strip() or None
    visitor: Optional[Visitor] = None
    if normalized_open_id or req.visitor_id is not None:
        visitor = (
            db.query(Visitor)
            .filter(
                Visitor.id == req.visitor_id
                if req.visitor_id is not None
                else Visitor.platform_open_id == normalized_open_id,
                Visitor.project_id == platform.project_id,
                Visitor.platform_id == platform.id,
                Visitor.deleted_at.is_(None),
            )
            .first()
        )
        if req.visitor_id is not None and (
            visitor is None
            or (
                normalized_open_id
                and visitor.platform_open_id != normalized_open_id
            )
        ):
            raise visitor_access_denied()

    profile_changed = False
    is_new_visitor = False
    proof = VisitorAccessInput(
        visitor_access_token=req.visitor_access_token,
        im_token=req.im_token,
        customer_sessions=req.customer_sessions,
    )
    if visitor is not None:
        # Ownership is checked before profiles, channels, notifications or IM credentials change.
        access_secret, access = registration_access(
            db, visitor, proof, new=False
        )
    elif (
        proof.customer_sessions
        or proof.visitor_access_token is not None
        or proof.im_token is not None
    ):
        raise visitor_access_denied()

    # if nickname provided, nickname_zh not provided, set nickname_zh to nickname
    if req.nickname and not req.nickname_zh:
        req.nickname_zh = req.nickname

    if not visitor:
        is_new_visitor = True
        # Get real IP and language (from request body or headers)
        real_ip = get_client_ip(request, req.ip_address)
        real_language = get_client_language(request, req.language)

        # Use visitor_service.create_visitor_with_channel for complete setup
        # If normalized_open_id is None, visitor.id will be used as platform_open_id
        visitor = await visitor_service.create_visitor_with_channel(
            db=db,
            platform=platform,
            platform_open_id=normalized_open_id,
            name=req.name,
            nickname=req.nickname,
            nickname_zh=req.nickname_zh,
            avatar_url=req.avatar_url,
            phone_number=req.phone_number,
            email=req.email,
            company=req.company,
            job_title=req.job_title,
            source=req.source,
            note=req.note,
            custom_attributes=req.custom_attributes,
            timezone=req.timezone,
            language=real_language,
            ip_address=real_ip,
        )
        origin.check(db)
        access_secret, access = registration_access(
            db, visitor, VisitorAccessInput(), new=True
        )
        db.commit()
        profile_changed = True
    else:
        # Update existing visitor with non-None fields provided in request
        update_data = req.model_dump(exclude_unset=True)

        # Handle nickname updates
        if "nickname" in update_data or "nickname_zh" in update_data:
            (
                resolved_nickname,
                resolved_nickname_zh,
            ) = visitor_service.resolve_visitor_nickname(
                update_data.get("nickname"),
                update_data.get("nickname_zh"),
                normalized_open_id or str(visitor.id),
            )
            update_data["nickname"] = resolved_nickname
            update_data["nickname_zh"] = resolved_nickname_zh

        updatable_fields = [
            "name",
            "nickname",
            "nickname_zh",
            "avatar_url",
            "phone_number",
            "email",
            "company",
            "job_title",
            "source",
            "note",
            "custom_attributes",
            "timezone",
            "language",
        ]

        # Handle IP address update (use request body or extract from headers)
        if "ip_address" in update_data:
            real_ip = get_client_ip(request, update_data.get("ip_address"))
            if real_ip:
                update_data["ip_address"] = real_ip
                updatable_fields.append("ip_address")

        # Handle language update (use request body or extract from headers)
        if "language" in update_data:
            real_language = get_client_language(
                request, update_data.get("language")
            )
            if real_language:
                update_data["language"] = real_language
        any_updated = False
        # Always update last_visit_time on engagement
        visitor.last_visit_time = datetime.utcnow()
        any_updated = True

        for field in updatable_fields:
            if field in update_data and update_data[field] is not None:
                setattr(visitor, field, update_data[field])
                any_updated = True
        if any_updated:
            db.commit()
            db.refresh(visitor)
            profile_changed = True

    system_info_changed = visitor_service.upsert_visitor_system_info(
        db, visitor, platform, req.system_info
    )
    if system_info_changed:
        db.commit()
        db.refresh(visitor)
        profile_changed = True

    # 3) Ensure WuKongIM channel exists
    # For new visitors, channel was already created by visitor_service.create_visitor_with_channel or visitor_service.ensure_visitor_channel
    # For existing visitors, we need to ensure channel exists
    if not is_new_visitor:
        origin.check(db)
        await visitor_service.ensure_visitor_channel(db, visitor, platform)
        origin.check(db)

    if profile_changed:
        origin.check(db)
        await notify_visitor_profile_updated(db, visitor)
        origin.check(db)

    # 3c) Register or login visitor to WuKongIM and generate token for IM login
    current_proof = VisitorAccessInput(
        visitor_access_token=access_secret,
        customer_sessions=req.customer_sessions,
    )
    origin.check(db)
    access = require_access(db, visitor, current_proof)
    db.commit()
    im_token = issue_visitor_im_token(
        visitor,
        timedelta(minutes=settings.SAAS_VISITOR_IM_TOKEN_MINUTES),
        business=access,
    )
    try:
        await wukongim_client.register_or_login_user(
            uid=str(visitor.id) + "-vtr",
            token=im_token,
        )
        logger.info(
            "WuKongIM user ensured for visitor",
            extra={"uid": str(visitor.id)},
        )
    except Exception as e:
        logger.error(
            "Failed to register/login visitor on WuKongIM",
            extra={"uid": str(visitor.id), "error_type": type(e).__name__},
        )

    origin.check(db)
    access = require_access(db, visitor, current_proof)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    resp, guard = registration_output(
        db, origin, visitor, im_token, access_secret, access, user_language
    )
    request.state.operation_guard = guard
    guard.check()
    return resp


@router.post(
    "/activities",
    response_model=VisitorActivityCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Visitor: Record Activity",
    description=(
        "Record a visitor activity from the client-side integration. "
        "Authenticate using the platform API key and supply the visitor ID."
    ),
)
async def record_visitor_activity(
    req: VisitorActivityCreateRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    x_platform_api_key: Optional[str] = Header(
        None, alias="X-Platform-API-Key"
    ),
    db: Session = Depends(get_db),
) -> VisitorActivityCreateResponse:
    """Record a visitor activity with platform API key authentication."""
    api_key = req.platform_api_key or x_platform_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing platform API key",
        )

    platform = (
        db.query(Platform)
        .filter(
            Platform.api_key == api_key,
            Platform.is_active == True,
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid platform API key",
        )

    access = require_visitor_data_access(db, platform, credentials)
    visitor = require_profile_visitor(db, access, req.visitor_id)
    guard = profile_guard(access, visitor_id=visitor.id)
    request.state.operation_guard = guard

    # Update visitor's last visit time
    visitor.last_visit_time = datetime.utcnow()
    db.add(visitor)
    # Don't commit yet, we'll commit with the activity

    data = req.model_dump(exclude_unset=True)

    activity: Optional[VisitorActivity] = None
    is_update = req.id is not None
    if is_update:
        activity = (
            db.query(VisitorActivity)
            .filter(
                VisitorActivity.id == req.id,
                VisitorActivity.project_id == platform.project_id,
                VisitorActivity.visitor_id == visitor.id,
                VisitorActivity.deleted_at.is_(None),
            )
            .first()
        )
        if not activity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Visitor activity not found",
            )

    occurred_at = (
        req.occurred_at
        if "occurred_at" in data and req.occurred_at is not None
        else (activity.occurred_at if activity else datetime.utcnow())
    )
    title = req.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Activity title cannot be blank",
        )

    context_payload = None
    context_provided = "context" in data
    if context_provided and req.context is not None:
        context_payload = req.context.model_dump(exclude_none=True)

    duration_provided = "duration_seconds" in data
    description_provided = "description" in data

    if not activity:
        activity = VisitorActivity(
            project_id=platform.project_id,
            visitor_id=visitor.id,
            activity_type=req.activity_type.value,
            title=title,
            occurred_at=occurred_at,
        )
        db.add(activity)

    activity.activity_type = req.activity_type.value
    activity.title = title
    activity.occurred_at = occurred_at

    if description_provided or not is_update:
        activity.description = req.description

    if duration_provided or not is_update:
        activity.duration_seconds = req.duration_seconds

    if context_provided:
        activity.context = context_payload
    elif not is_update:
        activity.context = None
    reauthorize_visitor_data(db, access)
    db.commit()
    db.refresh(activity)

    logger.info(
        "Recorded visitor activity",
        extra={
            "visitor_id": str(visitor.id),
            "platform_id": str(platform.id),
            "activity_type": activity.activity_type,
            "operation": "update" if is_update else "create",
        },
    )

    return VisitorActivityCreateResponse(
        id=activity.id,
        activity_type=activity.activity_type,
        title=activity.title,
        description=activity.description,
        occurred_at=activity.occurred_at,
        duration_seconds=activity.duration_seconds,
        context=activity.context,
    )


@router.post(
    "/{visitor_id}/accept",
    response_model=AcceptVisitorResponse,
    summary="接入访客",
    description="客服接入访客（无论访客是否在排队中）。如果访客正在排队，会自动更新其排队状态。",
)
async def accept_visitor_direct(
    visitor_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> AcceptVisitorResponse:
    """
    客服接入访客。
    
    - 无论访客是否在等待队列中均可接入
    - 如果访客在等待队列中，会自动更新其队列条目状态
    - 调用 transfer_to_staff 完成实际分配和 WuKongIM 关联
    """
    scope = begin_mutation(
        db,
        credentials,
        current_user,
        visitor_id,
        "visitors:update",
        claim=True,
    )

    # 1. 查找访客
    visitor = (
        db.query(Visitor)
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == current_user.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visitor not found",
        )

    # 1a. 检查访客服务状态，如果已经是 ACTIVE 则不允许再次接入
    if visitor.service_status == VisitorServiceStatus.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Visitor is already being served (status is active)",
        )

    # 2. 检查是否有处于等待中的队列条目
    queue_entry = (
        db.query(VisitorWaitingQueue)
        .filter(
            VisitorWaitingQueue.visitor_id == visitor_id,
            VisitorWaitingQueue.project_id == current_user.project_id,
            VisitorWaitingQueue.status == WaitingStatus.WAITING.value,
        )
        .order_by(VisitorWaitingQueue.entered_at.desc())
        .first()
    )

    # 3. 调用 transfer_to_staff 完成接入
    # transfer_to_staff 内部会处理：
    # - 状态迁移 (ACTIVE)
    # - 会话创建/更新 (VisitorSession)
    # - 历史记录 (VisitorAssignmentHistory)
    # - WuKongIM 频道订阅和系统消息
    result = await transfer_to_staff(
        db=db,
        visitor_id=visitor_id,
        project_id=current_user.project_id,
        source=AssignmentSource.MANUAL,
        assigned_by_staff_id=current_user.id,
        target_staff_id=current_user.id,  # 指定由当前客服接入
        platform_id=visitor.platform_id,
        ai_disabled=True,  # 人工接入通常禁用 AI
        add_to_queue_if_no_staff=False,
        notes=f"Accepted manually by {current_user.username}",
        expected_unassigned=True,
        mutation_scope=scope,
    )

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message or "Failed to accept visitor",
        )

    assert result.operation_guard is not None
    guard = result.operation_guard
    request.state.operation_guard = guard
    guard.check()

    # 4. 如果之前在排队，更新队列状态
    wait_duration: Optional[int] = 0
    if queue_entry:
        wait_duration = queue_entry.wait_duration_seconds
        # transfer_to_staff updates the queue under the visitor lock.

    logger.info(
        f"Staff {current_user.id} accepted visitor {visitor_id} (direct)",
        extra={
            "staff_id": str(current_user.id),
            "visitor_id": str(visitor_id),
            "has_queue": queue_entry is not None,
            "wait_duration": wait_duration,
        },
    )

    return AcceptVisitorResponse(
        success=True,
        message="访客已成功接入",
        entry_id=queue_entry.id if queue_entry else None,
        visitor_id=visitor_id,
        staff_id=current_user.id,
        session_id=result.session.id if result.session else None,
        channel_id=build_visitor_channel_id(visitor_id),
        channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        wait_duration_seconds=wait_duration,
    )


@router.get("/by-channel", response_model=VisitorResponse)
async def get_visitor_by_channel(
    request: Request,
    channel_id: str,
    channel_type: int,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    """Get visitor details by channel identifiers. Requires visitors:read permission.

    Logic:
    - channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE (251): channel_id format is "{visitor_uuid}-vtr"
    - channel_type == 1: channel_id is the visitor_id directly
    - otherwise: 400 unsupported channel type
    """
    # Resolve visitor_id from channel info
    if channel_type == CHANNEL_TYPE_CUSTOMER_SERVICE:
        try:
            visitor_uuid = parse_visitor_channel_id(channel_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid visitor channel_id format",
            )
    elif channel_type == 1:
        try:
            visitor_uuid = UUID(channel_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid visitor_id in channel",
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported channel_type",
        )

    access = require_staff_record_access(
        db, credentials, current_user, "visitors:read"
    )
    require_profile_visitor(db, access, visitor_uuid)
    guard = staff_record_guard(access, (visitor_uuid,), "visitors:read")
    request.state.operation_guard = guard
    guard.check()

    # Query visitor with eager-loaded platform to populate platform_type
    visitor = (
        db.query(Visitor)
        .options(selectinload(Visitor.platform))
        .filter(
            Visitor.id == visitor_uuid,
            Visitor.project_id == current_user.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )

    if not visitor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Visitor not found"
        )

    response = VisitorResponse.model_validate(visitor)
    populate_visitor_ai_settings(response, visitor.platform)
    localize_visitor_response_intent(
        response, request.headers.get("Accept-Language")
    )
    set_visitor_display_nickname(response, user_language)
    return response


@router.get(
    "/{visitor_id}/basic",
    response_model=VisitorBasicResponse | VisitorPublicProfile,
)
async def get_visitor_basic(
    visitor_id: UUID,
    request: Request,
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    x_platform_api_key: str | None = Header(None, alias="X-Platform-API-Key"),
    db: Session = Depends(get_db),
    user_language: UserLanguage = Depends(get_user_language),
) -> VisitorBasicResponse | VisitorPublicProfile:
    """Staff sees current owned details; a visitor sees only their own display."""
    access = require_profile_access(db, credentials, x_platform_api_key)
    visitor = require_profile_visitor(db, access, visitor_id)
    guard = (
        profile_guard(access, visitor_id=visitor_id)
        if isinstance(access, VisitorDataAccess)
        else staff_record_guard(access, (visitor_id,), "visitors:read")
    )
    request.state.operation_guard = guard
    guard.check()
    response = VisitorBasicResponse.model_validate(visitor)
    set_visitor_display_nickname(response, user_language)
    if isinstance(access, VisitorDataAccess):
        return VisitorPublicProfile(
            id=visitor.id,
            name=response.name,
            display_nickname=response.display_nickname,
            avatar_url=response.avatar_url,
            is_online=visitor.is_online,
        )
    populate_visitor_ai_settings(response, visitor.platform)
    return response


@router.get("/{visitor_id}", response_model=VisitorResponse)
async def get_visitor(
    request: Request,
    visitor_id: str,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:read")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    """Get visitor details. Requires visitors:read permission."""
    logger.info(f"User {current_user.username} getting visitor: {visitor_id}")

    access = require_staff_record_access(
        db, credentials, current_user, "visitors:read"
    )
    try:
        visitor_uuid = UUID(visitor_id)
    except (ValueError, AttributeError):
        raise HTTPException(404, "Visitor not found") from None
    require_profile_visitor(db, access, visitor_uuid)
    guard = staff_record_guard(access, (visitor_uuid,), "visitors:read")
    request.state.operation_guard = guard
    guard.check()

    visitor = None
    if visitor_uuid:
        visitor = (
            db.query(Visitor)
            .options(
                joinedload(Visitor.platform),
                selectinload(Visitor.visitor_tags).selectinload(
                    VisitorTag.tag
                ),
                selectinload(Visitor.ai_profile),
                selectinload(Visitor.ai_insight),
                selectinload(Visitor.system_info),
            )
            .filter(
                Visitor.id == visitor_uuid,
                Visitor.project_id == current_user.project_id,
                Visitor.deleted_at.is_(None),
            )
            .first()
        )
    if not visitor:
        raise HTTPException(404, "Visitor not found")

    active_tags = [
        vt.tag
        for vt in visitor.visitor_tags
        if vt.deleted_at is None and vt.tag and vt.tag.deleted_at is None
    ]
    tag_responses = [TagResponse.model_validate(tag) for tag in active_tags]
    set_tag_list_display_name(tag_responses, user_language)

    ai_profile_response = (
        VisitorAIProfileResponse.model_validate(visitor.ai_profile)
        if visitor.ai_profile
        else None
    )
    ai_insight_response = (
        VisitorAIInsightResponse.model_validate(visitor.ai_insight)
        if visitor.ai_insight
        else None
    )
    system_info_response = (
        VisitorSystemInfoResponse.model_validate(visitor.system_info)
        if visitor.system_info
        else None
    )

    recent_activities = (
        db.query(VisitorActivity)
        .filter(
            VisitorActivity.visitor_id == visitor.id,
            VisitorActivity.project_id == current_user.project_id,
            VisitorActivity.deleted_at.is_(None),
        )
        .order_by(VisitorActivity.occurred_at.desc())
        .limit(10)
        .all()
    )
    recent_activity_responses = [
        VisitorActivityResponse.model_validate(activity)
        for activity in recent_activities
    ]

    visitor_payload = VisitorResponse.model_validate(visitor)
    populate_visitor_ai_settings(visitor_payload, visitor.platform)
    response = visitor_payload.model_copy(
        update={
            "tags": tag_responses,
            "ai_profile": ai_profile_response,
            "ai_insights": ai_insight_response,
            "system_info": system_info_response,
            "recent_activities": recent_activity_responses,
        }
    )
    set_visitor_display_nickname(response, user_language)
    return response


@router.put("/{visitor_id}/attributes", response_model=VisitorResponse)
async def set_visitor_attributes(
    request: Request,
    visitor_id: UUID,
    attributes: VisitorAttributesUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:update"
    )
    updates = attributes.model_dump(exclude_unset=True)
    custom_attributes = updates.pop("custom_attributes", None)

    def apply(locked: Visitor) -> None:
        for field, value in updates.items():
            setattr(locked, field, value)
        if custom_attributes is not None:
            locked.custom_attributes = custom_attributes or {}

    visitor, output_scope = commit_change(db, scope, apply)
    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.patch("/{visitor_id}", response_model=VisitorResponse)
async def update_visitor(
    request: Request,
    visitor_id: UUID,
    visitor_data: VisitorUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:update"
    )
    updates = visitor_data.model_dump(exclude_unset=True)
    custom_attributes = updates.pop("custom_attributes", None)

    def apply(locked: Visitor) -> None:
        for field, value in updates.items():
            setattr(locked, field, value)
        if custom_attributes is not None:
            locked.custom_attributes = custom_attributes or {}

    visitor, output_scope = commit_change(db, scope, apply)
    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.post("/{visitor_id}/enable-ai", response_model=VisitorResponse)
async def enable_ai_for_visitor(
    request: Request,
    visitor_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:update"
    )

    try:
        await prepare_native_mode(scope.check(db, lock=True), "auto")
    except BaseException:
        db.rollback()
        raise

    def apply(locked: Visitor) -> None:
        locked.ai_disabled = False
        locked.service_mode = "auto"

    visitor, output_scope = commit_change(db, scope, apply)
    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.post("/{visitor_id}/disable-ai", response_model=VisitorResponse)
async def disable_ai_for_visitor(
    request: Request,
    visitor_id: UUID,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:update"
    )

    def apply(locked: Visitor) -> None:
        locked.ai_disabled = True
        locked.service_mode = "manual"
        locked.humanization_skill_enabled = False

    visitor, output_scope = commit_change(db, scope, apply)
    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.get(
    "/{visitor_id}/humanization-settings", response_model=EmployeeStyle
)
async def get_visitor_humanization_settings(
    visitor_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> EmployeeStyle:
    access = require_staff_record_access(
        db, credentials, current_user, "visitors:read"
    )
    require_profile_visitor(db, access, visitor_id)
    guard = staff_record_guard(access, (visitor_id,), "visitors:read")
    request.state.operation_guard = guard
    guard.check()
    visitor = (
        db.query(Visitor)
        .options(joinedload(Visitor.platform))
        .filter(
            Visitor.id == visitor_id,
            Visitor.project_id == current_user.project_id,
            Visitor.deleted_at.is_(None),
        )
        .first()
    )
    if not visitor:
        raise HTTPException(404, "Visitor not found")
    result = await resolve_employee_style(
        str(current_user.project_id),
        str(visitor.platform.agent_id)
        if visitor.platform and visitor.platform.agent_id
        else None,
        visitor.humanization_skill_name,
        visitor.humanization_skill_enabled,
    )
    guard.check()
    return result


@router.put("/{visitor_id}/service-mode", response_model=VisitorResponse)
async def update_visitor_service_mode(
    request: Request,
    visitor_id: UUID,
    data: VisitorServiceModeUpdate,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:update")),
    user_language: UserLanguage = Depends(get_user_language),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorResponse:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:update"
    )
    # The awaited skill check changes only a detached object, never the session's
    # pending ORM record. The current original customer is locked afterwards.
    staged = Visitor()
    try:
        await visitor_service.configure_service_mode(
            staged, str(scope.access.project_id), data
        )
    except ValueError as exc:
        scope.guard().check()
        raise HTTPException(422, detail=str(exc)) from exc

    try:
        await prepare_native_mode(
            scope.check(db, lock=True), staged.service_mode
        )
    except BaseException:
        db.rollback()
        raise

    def apply(locked: Visitor) -> None:
        locked.service_mode = staged.service_mode
        locked.ai_disabled = staged.ai_disabled
        locked.humanization_skill_name = staged.humanization_skill_name
        locked.humanization_skill_enabled = staged.humanization_skill_enabled

    visitor, output_scope = commit_change(db, scope, apply)
    return await _visitor_mutation_response(
        request, db, visitor, output_scope, user_language
    )


@router.delete("/{visitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_visitor(
    visitor_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: Staff = Depends(require_permission("visitors:delete")),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> None:
    scope = begin_mutation(
        db, credentials, current_user, visitor_id, "visitors:delete"
    )
    visitor, output_scope = commit_change(
        db,
        scope,
        lambda locked: setattr(locked, "deleted_at", datetime.utcnow()),
    )
    guard = output_scope.guard()
    request.state.operation_guard = guard
    guard.check()
    return None


@router.post(
    "/messages/sync",
    response_model=WuKongIMChannelMessageSyncResponse,
    summary="Visitor: Sync Channel Messages",
    description="Retrieve the current visitor's own history using the platform key and signed visitor Bearer grant.",
)
async def sync_visitor_channel_messages(
    req: VisitorMessageSyncRequest,
    response: Response,
    db: Session = Depends(get_db),
    x_platform_api_key: Optional[str] = Header(
        None, alias="X-Platform-API-Key"
    ),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> WuKongIMChannelMessageSyncResponse:
    """A public platform identifier never grants access to visitor messages."""
    # 1) Authenticate platform via API key (body param or header)
    api_key = req.platform_api_key or x_platform_api_key
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing platform_api_key",
        )

    platform = (
        db.query(Platform)
        .filter(
            Platform.api_key == api_key,
            Platform.is_active.is_(True),
            Platform.deleted_at.is_(None),
        )
        .first()
    )
    if not platform:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid platform_api_key",
        )

    if platform.type == PlatformType.WECHAT_LOCAL.value:
        await authorize_bridge_visitor(
            db, platform, req.channel_id, req.channel_type, credentials
        )
    access = require_visitor_data_access(db, platform, credentials)
    authorize_visitor_data_channel(access, req.channel_id, req.channel_type)
    login_uid = f"{access.visitor_id}-vtr"

    # Defaults and bounds
    start_seq = (
        req.start_message_seq if req.start_message_seq is not None else 0
    )
    end_seq = req.end_message_seq if req.end_message_seq is not None else 0
    limit = req.limit if req.limit is not None else 100
    pull_mode = req.pull_mode if req.pull_mode is not None else 0

    logger.info(
        "Visitor messages sync request",
        extra={
            "platform_id": str(platform.id),
            "channel_id": req.channel_id,
            "channel_type": req.channel_type,
            "start_message_seq": start_seq,
            "end_message_seq": end_seq,
            "limit": limit,
            "pull_mode": pull_mode,
        },
    )

    try:
        result = await wukongim_client.sync_channel_messages(
            login_uid=login_uid,
            channel_id=req.channel_id,
            channel_type=req.channel_type,
            start_message_seq=start_seq,
            end_message_seq=end_seq,
            limit=limit,
            pull_mode=pull_mode,
            include_event_meta=1,
            event_summary_mode="full",
        )
        reauthorize_visitor_data(db, access)
        require_owned_history_batch(
            access, req.channel_id, req.channel_type, result
        )
        msg_count = len(result.messages)
        logger.info(
            "Visitor messages sync success",
            extra={
                "platform_id": str(platform.id),
                "channel_id": req.channel_id,
                "channel_type": req.channel_type,
                "messages": msg_count,
            },
        )
        reconciled = await reconcile_reply_history(
            result,
            project_id=str(platform.project_id),
            channel_id=req.channel_id,
            channel_type=req.channel_type,
        )
        if platform.type == PlatformType.WECHAT_LOCAL.value:
            from app.services.wechat_staff_delivery import mask_bridge_history

            reconciled = mask_bridge_history(reconciled)
        reauthorize_visitor_data(db, access)
        require_owned_history_batch(
            access, req.channel_id, req.channel_type, reconciled
        )
        response.headers['Cache-Control'] = 'no-store'
        return reconciled
    except (HTTPException, TGOAPIException):
        raise
    except Exception as e:
        logger.error(f"Failed to sync channel messages for visitor: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync channel messages",
        )


# ============================================================================
# Visitor Avatar Upload Endpoints
# ============================================================================


@router.post(
    "/{visitor_id}/avatar",
    response_model=VisitorAvatarUploadResponse,
    summary="Upload visitor avatar",
    description="Staff authorization required; JPEG, PNG, GIF or WebP, up to 5MB.",
    tags=["Visitors"],
)
async def upload_visitor_avatar(
    visitor_id: UUID,
    request: Request,
    file: UploadFile = File(..., description="Avatar image file"),
    db: Session = Depends(get_db),
    current_user: Staff = Depends(get_current_active_user),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
) -> VisitorAvatarUploadResponse:
    from app.schemas.staff_data_access import StaffDataAccess
    from app.services.visitor_avatar_access import (
        save_avatar,
        avatar_guard,
        saved_image,
    )

    access = require_profile_access(db, credentials, None)
    if (
        not isinstance(access, StaffDataAccess)
        or access.staff_id != current_user.id
    ):
        raise visitor_access_denied()
    visitor, total, mime = await save_avatar(db, access, visitor_id, file)
    request.state.operation_guard = avatar_guard(
        access, visitor_id, visitor.avatar_url, saved_image(visitor)
    )
    absolute_avatar_url = (
        f"{settings.API_BASE_URL.rstrip('/')}{visitor.avatar_url}"
    )
    return VisitorAvatarUploadResponse(
        visitor_id=visitor_id,
        avatar_url=absolute_avatar_url,
        file_name=file.filename or "avatar.jpg",
        file_size=total,
        file_type=mime,
        uploaded_at=visitor.updated_at,
    )


@router.get(
    "/{visitor_id}/avatar",
    summary="Get private visitor avatar",
    description="Staff or original visitor authorization required in request headers. No public image access.",
    tags=["Visitors"],
    responses={
        200: {
            "content": {"image/*": {}},
            "description": "Authorized avatar image",
        }
    },
)
async def get_visitor_avatar(
    visitor_id: UUID,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials
    | None = Depends(HTTPBearer(auto_error=False)),
    platform_api_key: str | None = Header(None, alias="X-Platform-API-Key"),
    v: str | None = Query(None, max_length=128),
) -> Response:
    from app.services.visitor_avatar_access import avatar_response

    access = require_profile_access(db, credentials, platform_api_key)
    return avatar_response(db, access, visitor_id, v)

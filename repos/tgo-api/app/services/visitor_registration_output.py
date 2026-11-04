"""Keep registration tied to its original website and limit its response fields."""

from dataclasses import dataclass
import hmac
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.models import Platform, Project, Visitor
from app.schemas.visitor import resolve_display_nickname
from app.schemas.visitor_registration import VisitorRegisterResponse
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.profile_access import OperationGuard, profile_guard
from app.services.visitor_access import denied
from app.services.visitor_data_access import reauthorize_visitor_data
from app.services.visitor_end_grant import issue_visitor_end_token
from app.schemas.visitor_access import VisitorBusinessAccess
from app.utils.const import CHANNEL_TYPE_CUSTOMER_SERVICE
from app.utils.encoding import build_visitor_channel_id


@dataclass(frozen=True)
class RegistrationOrigin:
    project_id: UUID
    platform_id: UUID
    api_key: SecretStr
    platform_type: str = "website"

    def check(self, db: Session) -> None:
        platform = (
            db.query(Platform).join(Project, Platform.project_id == Project.id)
            .filter(
                Platform.id == self.platform_id,
                Platform.project_id == self.project_id,
                Platform.type == self.platform_type,
                Platform.is_active.is_(True),
                Platform.deleted_at.is_(None),
                Project.deleted_at.is_(None),
            ).populate_existing().first()
        )
        if platform is None or not hmac.compare_digest(platform.api_key, self.api_key.get_secret_value()):
            raise denied()


def registration_output(
    db: Session, origin: RegistrationOrigin, visitor: Visitor,
    im_token: str, access_secret: SecretStr, business: VisitorBusinessAccess,
    language: str,
) -> tuple[VisitorRegisterResponse, OperationGuard]:
    access = VisitorDataAccess(
        visitor_id=visitor.id, project_id=origin.project_id,
        platform_id=origin.platform_id, platform_api_key=origin.api_key,
        im_token=SecretStr(im_token),
    )
    current = reauthorize_visitor_data(db, access)
    response = VisitorRegisterResponse(
        id=current.id, project_id=current.project_id, platform_id=current.platform_id,
        platform_open_id=current.platform_open_id,
        name=current.name, nickname=current.nickname, nickname_zh=current.nickname_zh,
        display_nickname=resolve_display_nickname(current.nickname, current.nickname_zh, language),
        avatar_url=current.avatar_url, is_online=current.is_online,
        first_visit_time=current.first_visit_time, last_visit_time=current.last_visit_time,
        last_offline_time=current.last_offline_time,
        created_at=current.created_at, updated_at=current.updated_at,
        channel_id=build_visitor_channel_id(current.id), channel_type=CHANNEL_TYPE_CUSTOMER_SERVICE,
        im_token=SecretStr(im_token), visitor_access_token=access_secret,
        visitor_end_token=issue_visitor_end_token(current),
        business_protected=bool(business.references), business_access_expires_at=business.expires_at,
    )
    return response, profile_guard(access, visitor_id=current.id)

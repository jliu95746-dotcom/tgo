"""Independent authentication and inventory for the 域见 operations console."""

from fastapi import APIRouter, Depends, Query, Request, Response, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.platform_operator import PlatformOperator
from app.schemas.operations import (
    MigrationPreviewResponse,
    OperationsStatus,
    OperatorLoginRequest,
    OperatorLoginResponse,
    OperatorResponse,
)
from app.services.operations_auth import (
    authenticate_operator,
    issue_operator_token,
    require_operations_enabled,
    resolve_operator_token,
    signing_key,
    unauthorized,
)
from app.services.operations_login_limit import limit_operator_login
from app.services.operations_preview import preview_company_migration
from app.schemas.commercial_readiness import CommercialReadiness
from app.services.commercial_readiness import readiness

router = APIRouter()
operator_bearer = HTTPBearer(auto_error=False)


def require_commercial_operations() -> None:
    """Configuration must be possible before enforcing customer subscriptions."""
    if not settings.SAAS_ENABLED:
        raise HTTPException(404, "企业商业化配置尚未启用")


def require_operator(
    credentials: HTTPAuthorizationCredentials | None = Depends(
        operator_bearer,
    ),
    db: Session = Depends(get_db),
) -> PlatformOperator:
    require_operations_enabled()
    if credentials is None:
        raise unauthorized()
    return resolve_operator_token(db, credentials.credentials)


@router.get("/status", response_model=OperationsStatus)
def operations_status() -> OperationsStatus:
    return OperationsStatus(
        enabled=settings.OPS_ENABLED,
        login_available=(
            settings.OPS_ENABLED and settings.OPS_SECRET_KEY is not None
        ),
    )


@router.get("/commercial-readiness", response_model=CommercialReadiness, dependencies=[Depends(require_operator)])
def commercial_readiness(db: Session = Depends(get_db)) -> CommercialReadiness:
    return readiness(db)


@router.post("/login", response_model=OperatorLoginResponse)
async def login_operator(
    payload: OperatorLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> OperatorLoginResponse:
    signing_key()
    await limit_operator_login(
        request.client.host if request.client else "unknown",
        payload.email,
    )
    operator = authenticate_operator(
        db, payload.email, payload.password.get_secret_value()
    )
    response.headers["Cache-Control"] = "no-store"
    return issue_operator_token(operator)


@router.get("/me", response_model=OperatorResponse)
def current_operator(
    operator: PlatformOperator = Depends(require_operator),
) -> OperatorResponse:
    return OperatorResponse.model_validate(operator)


@router.post("/logout", status_code=204)
def logout_operator(
    operator: PlatformOperator = Depends(require_operator),
    db: Session = Depends(get_db),
) -> Response:
    db.execute(
        update(PlatformOperator)
        .where(
            PlatformOperator.id == operator.id,
        )
        .values(token_version=PlatformOperator.token_version + 1)
    )
    db.commit()
    return Response(status_code=204)


@router.get("/migration-preview", response_model=MigrationPreviewResponse)
def migration_preview(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    operator: PlatformOperator = Depends(require_operator),
    db: Session = Depends(get_db),
) -> MigrationPreviewResponse:
    return preview_company_migration(db, limit, offset)

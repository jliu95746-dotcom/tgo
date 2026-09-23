"""Security utilities for authentication and authorization."""

from datetime import datetime, timedelta
from typing import Any, Dict, Literal, Optional, Union
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.logging import get_logger
from app.models import Project, Staff, Permission, RolePermission, ProjectRolePermission

logger = get_logger("security")

# Admin role constant - admins have all permissions
ADMIN_ROLE = "admin"

# Supported user languages
UserLanguage = Literal["zh", "en"]
DEFAULT_LANGUAGE: UserLanguage = "en"


def get_user_language(
    x_user_language: Optional[str] = Header(None, alias="x-user-language"),
) -> UserLanguage:
    """
    Get user language from request header.

    Args:
        x_user_language: Language code from 'x-user-language' header

    Returns:
        'zh' for Chinese, 'en' for English (default)
    """
    if x_user_language and x_user_language.lower() == "zh":
        return "zh"
    return "en"

# Password hashing
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12
)

# JWT token security
security = HTTPBearer()


def create_access_token(
    subject: Union[str, Any],
    project_id: Optional[Union[str, UUID]] = None,
    role: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 1,
) -> str:
    """Create JWT access token with optional project_id and role."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire, "sub": str(subject), "type": "staff_access",
        "token_version": token_version,
    }

    # Include project_id in token claims if provided
    if project_id:
        to_encode["project_id"] = str(project_id)
    
    # Include role in token claims if provided
    if role:
        to_encode["role"] = role

    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash."""
    return pwd_context.hash(password)


def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Accept staff tokens and legacy untyped tokens, never other purposes."""
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if payload.get("type") not in (None, "staff_access"):
            return None
        return payload
    except JWTError as e:
        # Downgrade to debug to avoid noisy logs on unauthenticated endpoints
        logger.debug(f"Token verification failed: {e}")
        return None


def resolve_staff_token(db: Session, token: str) -> Optional[Staff]:
    """Resolve current membership; signed claims cannot replace DB authority."""
    payload = verify_token(token)
    if payload is None:
        return None
    username = payload.get("sub")
    if not isinstance(username, str) or not username:
        return None
    user = db.query(Staff).filter(
        Staff.username == username,
        Staff.deleted_at.is_(None),
        Staff.project_id.in_(
            select(Project.id).where(Project.deleted_at.is_(None))
        ),
    ).first()
    if user is None:
        return None
    if not user.account_enabled:
        return None
    version = payload.get("token_version", 1)
    if type(version) is not int or version != user.token_version:
        return None
    claimed_project = payload.get("project_id")
    if claimed_project is not None:
        try:
            if UUID(str(claimed_project)) != user.project_id:
                return None
        except (ValueError, TypeError, AttributeError):
            return None
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> Staff:
    """Get current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    user = resolve_staff_token(db, credentials.credentials)
    if user is None:
        logger.info("Token verification failed: User not found")
        raise credentials_exception
    
    return user


async def get_current_active_user(
    current_user: Staff = Depends(get_current_user),
) -> Staff:
    """Get current active user."""
    if current_user.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


def authenticate_user(db: Session, username: str, password: str) -> Optional[Staff]:
    """Authenticate user with username and password."""
    user = db.query(Staff).filter(
        Staff.username == username,
        Staff.deleted_at.is_(None)
    ).first()

    # Public registration normalizes email usernames. Keep exact legacy logins first.
    if not user and "@" in username:
        user = db.query(Staff).filter(
            func.lower(Staff.username) == username.strip().lower(),
            Staff.deleted_at.is_(None),
        ).first()
    
    if not user:
        return None

    if not user.account_enabled:
        return None
    
    if not verify_password(password, user.password_hash):
        return None
    
    return user



def get_project_by_id(db: Session, project_id: Union[str, UUID]) -> Optional[Project]:
    """Get project by ID."""
    try:
        identifier = UUID(str(project_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return db.query(Project).filter(
        Project.id == identifier,
        Project.deleted_at.is_(None)
    ).first()




def generate_api_key() -> str:
    """Generate a new API key."""
    import secrets
    import string

    # Generate a random string for the API key
    alphabet = string.ascii_letters + string.digits
    api_key = "ak_live_" + "".join(secrets.choice(alphabet) for _ in range(32))
    return api_key


async def get_authenticated_project(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer(auto_error=True)),
    db: Session = Depends(get_db),
) -> tuple[Project, str]:
    """
    Get authenticated project via JWT token.

    This function authenticates the request using a JWT Bearer token and returns
    the associated project along with its API key for forwarding to downstream services.

    Authentication Method:
        - JWT Bearer token (required)
        - Current staff membership determines the project
        - Any project_id claim must match that current membership

    Return Value:
        The second element of the tuple (api_key_for_forwarding) is the project's API key,
        which should be used when calling downstream services (AI Service, RAG Service).
        This is NOT used for authenticating requests to tgo-api itself.

    Returns:
        tuple[Project, str]: (authenticated_project, api_key_for_downstream_services)

    Raises:
        HTTPException: 401 if JWT authentication fails or project not found

    Example:
        ```python
        project, downstream_api_key = Depends(get_authenticated_project)

        # Use project for business logic
        logger.info(f"Request for project: {project.id}")

        # Use downstream_api_key when calling AI Service or RAG Service
        await ai_client.create_agent(
            project_id=str(project.id),
            agent_data=data
        )
        ```
    """
    user = await get_current_user(credentials=credentials, db=db)
    project = get_project_by_id(db, user.project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Project not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Use project's API key for forwarding to external services
    api_key_for_forwarding = project.api_key
    logger.debug(f"Authenticated via JWT for project: {project.id}")

    return project, api_key_for_forwarding


def check_user_permission(
    db: Session,
    user: Staff,
    permission: str,
) -> bool:
    """
    Check if user has the specified permission.
    
    Uses MERGE mode: Final permissions = Global RolePermission + Project ProjectRolePermission
    Projects can only ADD permissions, not disable global ones.
    
    Args:
        db: Database session
        user: Staff user to check
        permission: Permission code in resource:action format (e.g., "staff:create")
    
    Returns:
        True if user has permission, False otherwise
    """
    # Admin has all permissions
    if user.role == ADMIN_ROLE:
        return True
    
    # Parse permission code
    try:
        resource, action = permission.split(":")
    except ValueError:
        logger.warning(f"Invalid permission format: {permission}")
        return False
    
    # Check global RolePermission (inherited by all projects)
    has_global_permission = db.query(RolePermission).join(Permission).filter(
        RolePermission.role == user.role,
        Permission.resource == resource,
        Permission.action == action,
    ).first()
    
    if has_global_permission:
        return True
    
    # Check project-specific ProjectRolePermission (additional permissions)
    has_project_permission = db.query(ProjectRolePermission).join(Permission).filter(
        ProjectRolePermission.role == user.role,
        ProjectRolePermission.project_id == user.project_id,
        Permission.resource == resource,
        Permission.action == action,
    ).first()
    
    return has_project_permission is not None


def require_permission(permission: str):
    """
    Create a dependency that requires the specified permission.
    
    This is a dependency factory that creates a FastAPI dependency
    to check if the current user has the specified permission.
    
    Args:
        permission: Permission code in resource:action format (e.g., "staff:create")
    
    Returns:
        A FastAPI dependency function
    
    Example:
        ```python
        @router.post("/staff")
        async def create_staff(
            current_user: Staff = Depends(require_permission("staff:create")),
        ):
            # Only users with staff:create permission can access
            pass
        ```
    """
    async def permission_dependency(
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: Session = Depends(get_db),
    ) -> Staff:
        """Check user has required permission."""
        user = await get_current_user(credentials=credentials, db=db)
        
        # Check if user is active
        if user.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inactive user"
            )
        
        # Check permission
        if settings.SAAS_BILLING_ENABLED and user.role != "admin" and permission in {
            "staff:create", "staff:update", "staff:delete",
            "projects:create", "projects:update", "projects:delete",
            "platforms:create", "platforms:update", "platforms:delete",
        }:
            raise HTTPException(403, "仅企业管理员可以执行此操作")
        if not check_user_permission(db, user, permission):
            logger.warning(
                f"Permission denied: user {user.username} lacks permission {permission}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {permission}"
            )
        
        return user
    
    return permission_dependency


def require_admin():
    """
    Create a dependency that requires admin role.
    
    This is a convenience function for endpoints that should only
    be accessible by admin users.
    
    Returns:
        A FastAPI dependency function
    
    Example:
        ```python
        @router.delete("/staff/{staff_id}")
        async def delete_staff(
            current_user: Staff = Depends(require_admin()),
        ):
            # Only admin users can access
            pass
        ```
    """
    async def admin_dependency(
        current_user: Staff = Depends(get_current_active_user),
    ) -> Staff:
        """Check user is admin."""
        if current_user.role != ADMIN_ROLE:
            logger.warning(
                f"Admin required: user {current_user.username} is not admin"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin permission required"
            )
        return current_user
    
    return admin_dependency


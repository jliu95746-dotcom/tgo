"""API v1 routes."""

from fastapi import APIRouter

from app.api.v1 import devices, device_screen_policies, mcp, sessions
from app.api.v1 import device_script_catalogs
from app.api.v1 import device_script_policies
from app.api.v1 import device_script_executions

router = APIRouter()

router.include_router(devices.router, prefix="/devices", tags=["devices"])
router.include_router(
    device_screen_policies.router, prefix="/devices", tags=["devices"]
)
router.include_router(
    device_script_catalogs.router, prefix="/devices", tags=["devices"]
)
router.include_router(
    device_script_policies.router, prefix="/devices", tags=["devices"]
)
router.include_router(
    device_script_executions.router, prefix="/devices", tags=["devices"]
)
router.include_router(mcp.router, prefix="/mcp", tags=["mcp"])
router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])

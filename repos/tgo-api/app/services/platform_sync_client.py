"""Client for synchronizing Platform records to the TGO Platform Service.

This module encapsulates HTTP calls to the external Platform Service so callers
can focus on business logic. Endpoints are derived from settings and the
platform service OpenAPI (docs/api_platform_service.json).

Current implementation uses POST /v1/platforms with the record body.
If the remote service supports update/delete via REST, you can extend methods
accordingly (e.g., PATCH/DELETE /v1/platforms/{id}).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from app.core.config import settings


def _platform_to_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map local Platform fields to Platform Service request body.

    The OpenAPI specifies PlatformCreateRequest fields.
    We include id to allow idempotent upsert behavior.
    """
    payload: Dict[str, Any] = {
        "id": str(data["id"]) if data.get("id") else None,
        "project_id": str(data["project_id"]),
        "name": data["name"],
        "type": data["type"],
        "config": data.get("config"),
        "is_active": data.get("is_active", True),
        "api_key": data.get("api_key"),
    }
    return payload


class PlatformSyncClient:
    def __init__(self) -> None:
        self.base_url = settings.PLATFORM_SERVICE_URL.rstrip("/")
        self.timeout = settings.PLATFORM_SERVICE_TIMEOUT
        self.api_key = settings.PLATFORM_SERVICE_API_KEY

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def upsert_platform(
        self, platform_data: Dict[str, Any]
    ) -> httpx.Response:
        """Create or upsert a platform via POST /v1/platforms.

        The remote service auto-generates id if omitted; we include id to keep
        records synchronized across services.
        """
        url = f"{self.base_url}/v1/platforms"
        payload = _platform_to_payload(platform_data)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url, json=payload, headers=self._headers()
            )
        return resp

    async def delete_platform(
        self, platform_id: str
    ) -> Optional[httpx.Response]:
        (
            'Attempt to delete a platform. If DELETE is unsupported, return '
            'None.\n'
            '\n'
            '        We try DELETE /v1/platforms/{id}. If the endpoint '
            "doesn't exist, callers\n"
            '        should fall back to a soft-delete upsert (deleted_at '
            'set, or is_active=False).\n'
            '        '
        )
        url = f"{self.base_url}/v1/platforms/{platform_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.delete(url, headers=self._headers())
                return resp
            except httpx.HTTPStatusError:
                raise
            except Exception:
                return None


platform_sync_client = PlatformSyncClient()

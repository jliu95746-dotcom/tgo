"""Only authenticated API forwarding can supply an employee activation actor."""

from uuid import UUID

from fastapi import HTTPException, Query, Request

from app.api.v1.agent_trials import require_trial_principal


async def optional_mutation_actor(request: Request, project_id: UUID = Query(...)) -> UUID | None:
    headers = ("X-Internal-API-Key", "X-Project-Id", "X-Staff-Id")
    if not any(request.headers.get(name) for name in headers):
        # Legacy internal callers can save paused drafts and revoke resources.
        # The business service refuses activation without a verified actor.
        return None
    principal = await require_trial_principal(request)
    if principal.project_id != project_id:
        raise HTTPException(403, "员工配置的企业身份不一致")
    return principal.staff_id

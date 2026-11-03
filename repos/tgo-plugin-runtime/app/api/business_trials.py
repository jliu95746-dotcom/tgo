"""Dedicated private purpose; normal plugin/config routes cannot run this test."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.connection_tests import PrivateTestRoute, require_connection_service
from app.domain.business_tools.trial_runner import BusinessTrialRunner
from app.schemas.agent_business_trials import BusinessTrialCommand, BusinessTrialReceipt

router = APIRouter(
    prefix="/business/trials",
    route_class=PrivateTestRoute,
    dependencies=[Depends(require_connection_service)],
)


def get_runner() -> BusinessTrialRunner:
    return BusinessTrialRunner()


@router.post("/query", response_model=BusinessTrialReceipt)
async def query(
    command: BusinessTrialCommand, runner: BusinessTrialRunner = Depends(get_runner)
) -> BusinessTrialReceipt:
    try:
        return await runner.run(command)
    except Exception:
        raise HTTPException(503, "业务测试原配置、身份或结果暂不可确认") from None

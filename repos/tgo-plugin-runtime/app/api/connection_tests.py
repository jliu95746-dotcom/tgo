"""Private, purpose-limited entry for company connection configuration te\
sts."""

from collections.abc import Callable, Coroutine
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.domain.business_tools.connection_runner import ConnectionTestRunner
from app.schemas.connection_test_protocol import (
    ConnectionTestCommand,
    ConnectionTestReceipt,
)
from app.services.business_connection_client import (
    ConnectionAuthorityUnavailable,
)


class PrivateTestRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[None, None, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {"loc": list(item["loc"]), "type": item["type"]}
                            for item in error.errors()
                        ]
                    },
                )

        return safe_handler


def require_connection_service(
    provided: str
    | None = Header(default=None, alias="X-Business-Service-Token"),
) -> None:
    configured = settings.BUSINESS_CONNECTION_SERVICE_TOKEN
    if (
        configured is None
        or len(configured.get_secret_value()) < 32
        or not provided
        or not hmac.compare_digest(
            configured.get_secret_value().encode(), provided.encode()
        )
    ):
        raise HTTPException(401, "Invalid business service token")


def get_connection_test_runner() -> ConnectionTestRunner:
    return ConnectionTestRunner()


router = APIRouter(
    prefix="/business/connections",
    tags=["private-connection-tests"],
    route_class=PrivateTestRoute,
    dependencies=[Depends(require_connection_service)],
)


@router.post("/test", response_model=ConnectionTestReceipt)
async def test_connection(
    command: ConnectionTestCommand,
    runner: ConnectionTestRunner = Depends(get_connection_test_runner),
) -> ConnectionTestReceipt:
    try:
        result = await runner.run(command)
        if not result.matches(command):
            raise ConnectionAuthorityUnavailable()
        return result
    except Exception:
        # Upstream exceptions can include credentials, inputs or provider
        # bodies.
        raise HTTPException(503, "业务连接测试暂不可用，请查询原请求后重试") from None

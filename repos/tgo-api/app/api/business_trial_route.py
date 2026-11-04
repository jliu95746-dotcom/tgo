"""Validation responses must not echo customer sessions or order inputs."""

from collections.abc import Callable, Coroutine

from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class BusinessTrialRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as error:
                return JSONResponse(
                    status_code=422,
                    content={
                        "detail": [
                            {
                                "loc": list(item["loc"]),
                                "type": item["type"],
                                "msg": "业务测试字段无效",
                            }
                            for item in error.errors()
                        ]
                    },
                )

        return safe

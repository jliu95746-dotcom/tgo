"""Keep signed customer credentials out of visitor chat validation errors."""

from collections.abc import Callable, Coroutine

from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from app.schemas.channel_chat_access import ChannelChatAccess
from app.schemas.visitor_data_access import VisitorDataAccess
from app.services.visitor_chat_access import AuthorizedChatResponse
from app.services.profile_access import OperationGuard
from app.api.current_access_route import CurrentAccessResponse


class BusinessContextChatRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def safe(request: Request) -> Response:
            try:
                response = await handler(request)
                guard = getattr(request.state, "operation_guard", None)
                if isinstance(guard, OperationGuard):
                    response.headers["Cache-Control"] = "no-store"
                    return CurrentAccessResponse(response, guard)
                access = getattr(request.state, "visitor_chat_access", None)
                if isinstance(access, (VisitorDataAccess, ChannelChatAccess)):
                    return AuthorizedChatResponse(response, access)
                return response
            except RequestValidationError as error:
                body = error.body
                if not ((isinstance(body, dict) and "customer_sessions" in body)
                        or (isinstance(body, str) and "customer_sessions" in body)):
                    raise
                return JSONResponse(status_code=422, content={"detail": [
                    {"loc": list(item["loc"]), "type": item["type"], "msg": "客户会话验证信息无效"}
                    for item in error.errors()]})

        return safe

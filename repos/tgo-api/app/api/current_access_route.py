"Recheck server-captured authority at response headers and body \
boundaries."

from collections.abc import Callable, Coroutine
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from app.services.profile_access import OperationGuard


class CurrentAccessResponse(Response):
    def __init__(self, response: Response, guard: OperationGuard):
        super().__init__(
            status_code=response.status_code, headers=dict(response.headers)
        )
        self.response = response
        self.guard = guard

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        async def checked_send(message: Message) -> None:
            if message["type"] in (
                "http.response.start",
                "http.response.body",
            ):
                self.guard.check()
            await send(message)

        # File transfer shortcuts bypass the body boundary guard.
        safe_scope = dict(scope)
        safe_scope["extensions"] = {
            key: value
            for key, value in scope.get("extensions", {}).items()
            if key != "http.response.pathsend"
        }
        await self.response(safe_scope, receive, checked_send)


class CurrentAccessRoute(APIRoute):
    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[object, object, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            result = await handler(request)
            guard = getattr(request.state, "operation_guard", None)
            if isinstance(guard, OperationGuard):
                result.headers["Cache-Control"] = "no-store"
                return CurrentAccessResponse(result, guard)
            return result

        return checked

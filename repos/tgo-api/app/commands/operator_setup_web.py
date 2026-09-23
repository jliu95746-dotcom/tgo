"""Temporary loopback operator setup launched by a local administrator."""

import argparse
import asyncio
import hmac
import logging
import socket
import sys
import time
from collections.abc import Callable
from html import escape
from secrets import token_urlsafe
from urllib.parse import parse_qs, urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from pydantic import SecretStr, ValidationError

from app.core.exceptions import TGOAPIException
from app.schemas.operations import OperatorCreateRequest

STYLES = """
body{margin:0;background:#f3f6fa;color:#172434;font:16px system-ui,sans-serif}
main{max-width:430px;margin:7vh auto;padding:36px;background:white;
border:1px solid #e2e8f0;border-radius:18px;box-shadow:0 12px 40px #18324a0a}
h1{font-size:26px;margin:8px 0 18px}.brand{color:#087f83;font-weight:700}
p{line-height:1.7;color:#526275}label{display:block;margin:22px 0 8px}
input{box-sizing:border-box;width:100%;padding:13px;border:1px solid #b9c6d5;
border-radius:8px;font:inherit}button{width:100%;margin-top:26px;padding:14px;
border:0;border-radius:8px;background:#087f83;color:white;font:inherit;
cursor:pointer}.error{color:#b42318}.note{font-size:13px}
@media(max-width:540px){main{margin:22px 16px;padding:24px}}
"""


def build_setup_app(
    email: str,
    name: str,
    origin: str,
    create: Callable[[OperatorCreateRequest], object],
    *,
    lifetime_seconds: int = 900,
) -> FastAPI:
    """No public registration route: host, origin, nonce and lifetime bound."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.finished = asyncio.Event()
    deadline = time.monotonic() + lifetime_seconds
    nonce = token_urlsafe(32)
    lock = asyncio.Lock()
    expected_host = urlsplit(origin).netloc

    def page(message: str = "", status: int = 200) -> HTMLResponse:
        complete = app.state.finished.is_set()
        body = (
            "<h1>账号已创建</h1><p>密码设置成功，可以关闭此页面。" "运营登录入口还需完成配置。</p>"
            if complete
            else "<h1>设置运营管理员密码</h1>"
            f"<p>管理员名称：<strong>{escape(name)}</strong><br>"
            f"登录邮箱：<strong>{escape(email)}</strong></p>"
            f'<p class="error" role="alert">{escape(message)}</p>'
            '<form action="/create" method="post">'
            f'<input type="hidden" name="nonce" value="{nonce}">'
            '<label for="password">新密码</label>'
            '<input id="password" name="password" type="password" '
            'autocomplete="new-password" minlength="12" maxlength="72" '
            "required autofocus>"
            '<label for="confirm">再次输入密码</label>'
            '<input id="confirm" name="confirm" type="password" '
            'autocomplete="new-password" minlength="12" maxlength="72" '
            'required><button type="submit">设置密码并创建账号</button></form>'
            '<p class="note">至少 12 位，UTF-8 编码不超过 72 字节。'
            "请直接在此页面填写，密码不要发送到聊天中。"
            "本机设置页有效期 15 分钟。</p>"
        )
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" '
            'content="width=device-width,initial-scale=1">'
            f"<title>域见 · 设置管理员密码</title><style>{STYLES}</style>"
            '</head><body><main><div class="brand">域见 · 平台初始化</div>'
            f"{body}</main></body></html>",
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "form-action 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'"
                ),
            },
        )

    def reject(request: Request) -> HTMLResponse | None:
        if (
            request.headers.get("host") != expected_host
            or request.client is None
            or request.client.host not in {"127.0.0.1", "::1"}
        ):
            return HTMLResponse("仅限本机访问", status_code=403)
        if time.monotonic() >= deadline or app.state.finished.is_set():
            return HTMLResponse("设置入口已关闭", status_code=410)
        return None

    async def form(request: Request) -> HTMLResponse:
        denied = reject(request)
        return denied if denied is not None else page()

    async def submit(request: Request) -> HTMLResponse:
        async with lock:
            denied = reject(request)
            if denied is not None:
                return denied
            if request.headers.get("origin") != origin:
                return HTMLResponse("请求来源无效", status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != (
                "application/x-www-form-urlencoded"
            ):
                return HTMLResponse("请求格式无效", status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 4096:
                    return HTMLResponse("提交内容过长", status_code=413)
            try:
                values = parse_qs(body.decode("utf-8"), max_num_fields=3)
            except (UnicodeError, ValueError):
                return page("提交内容无效，请重新填写。", 400)
            if set(values) != {"nonce", "password", "confirm"} or any(
                len(value) != 1 for value in values.values()
            ):
                return page("提交内容无效，请重新填写。", 400)
            if not hmac.compare_digest(
                values["nonce"][0].encode("utf-8"), nonce.encode("utf-8"),
            ):
                return HTMLResponse("设置凭据无效，请刷新页面", status_code=403)
            if values["password"][0] != values["confirm"][0]:
                return page("两次密码不一致，请重新填写。", 400)
            try:
                data = OperatorCreateRequest(
                    email=email,
                    name=name,
                    password=SecretStr(values["password"][0]),
                )
                create(data)
            except ValidationError:
                return page("密码至少 12 位，UTF-8 编码不能超过 72 字节。", 400)
            except TGOAPIException as exc:
                return page("账号已存在或无法创建，请联系助手核对。", exc.status_code)
            except Exception:
                return page("创建失败，请联系助手核对服务状态。", 500)
            app.state.finished.set()
            return page()

    app.add_api_route("/", form, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route(
        "/create", submit, methods=["POST"], response_class=HTMLResponse,
    )
    return app


async def serve(email: str, name: str, port: int) -> None:
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.platform_operator import PlatformOperator
    from app.services.operations_auth import create_operator

    with SessionLocal() as db:
        if (
            db.scalar(
                select(PlatformOperator.id).where(
                    PlatformOperator.email == email.lower(),
                )
            )
            is not None
        ):
            raise ValueError("Operator already exists")

    def create(data: OperatorCreateRequest) -> object:
        with SessionLocal() as db:
            return create_operator(db, data)

    listener = socket.socket()
    listener.bind(("127.0.0.1", port))
    listener.listen(8)
    listener.setblocking(False)
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    app = build_setup_app(email, name, origin, create)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            access_log=False,
            log_level="critical",
            lifespan="off",
        )
    )

    async def close_after_setup() -> None:
        try:
            await asyncio.wait_for(app.state.finished.wait(), 900)
            await asyncio.sleep(3)
        except TimeoutError:
            pass
        server.should_exit = True

    watcher = asyncio.create_task(close_after_setup())
    sys.stdout.write(f"SETUP_URL={origin}/\n")
    sys.stdout.flush()
    try:
        await server.serve(sockets=[listener])
    finally:
        watcher.cancel()
        listener.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="本机临时运营密码设置网页")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(serve(args.email, args.name, args.port))
    except Exception as exc:
        sys.stderr.write(f"设置服务未启动（{type(exc).__name__}）。\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

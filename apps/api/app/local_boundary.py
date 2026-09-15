"""本地模式只接受由本机 BFF 签入的请求，保留账号模式原行为。"""

from secrets import compare_digest

from fastapi import Request
from fastapi.responses import JSONResponse

from app.config import settings


def local_request_allowed(request: Request) -> bool:
    if settings.app_mode != "local":
        return True
    token = request.headers.get("x-local-runtime-token", "")
    origin = request.headers.get("origin")
    return (
        request.url.hostname in {"localhost", "127.0.0.1", "::1"}
        and len(token) == 64
        and token.isascii()
        and compare_digest(token, settings.local_runtime_token.get_secret_value())
        and (origin is None or origin in settings.login_allowed_origins)
    )


async def local_access_boundary(request: Request, call_next):
    # 本地模式下不提供注册、登录与退出账号入口。
    if settings.app_mode == "local" and (
        not local_request_allowed(request) or request.url.path in {
            "/auth/login", "/auth/register", "/auth/logout"
        }
    ):
        return JSONResponse(
            {"code": "local_access_rejected", "message": "本地服务请求被拒绝"},
            status_code=403, headers={"Cache-Control": "no-store"},
        )
    return await call_next(request)

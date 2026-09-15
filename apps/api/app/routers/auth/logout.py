"""登出 HTTP 接口：来源校验、服务端撤销与清除 Cookie。"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import SecretStr

from app.config import LOGIN_COOKIE_NAME, settings
from app.database import SessionLocal
from app.schemas import LogoutErrorResponse
from app.services.auth.logout_service import logout_user


logger = logging.getLogger(__name__)


def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """只返回明确允许公开的错误信息。"""

    payload = LogoutErrorResponse(code=code, message=message)

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"Cache-Control": "no-store"},
    )


class LogoutRoute(APIRoute):
    """在来源校验、业务执行和响应生成外层处理错误。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                origin = request.headers.get("origin")

                if origin not in settings.login_allowed_origins:
                    return _error_response(
                        status.HTTP_403_FORBIDDEN,
                        code="logout_origin_rejected",
                        message="登出请求来源不被允许",
                    )

                response = await original_handler(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                logger.error("logout_failed")
                return _error_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="logout_failed",
                    message="登出失败，请稍候再试",
                )

        return safe_handler


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    route_class=LogoutRoute,
)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses={
        403: {
            "model": LogoutErrorResponse,
            "description": "请求来源不被允许",
        },
        500: {
            "model": LogoutErrorResponse,
            "description": "登出失败",
        },
    },
)
def logout(request: Request) -> Response:
    """服务完成后才清 Cookie；无记录或重复登出也返回成功。"""

    raw_token = request.cookies.get(LOGIN_COOKIE_NAME)
    token = SecretStr(raw_token) if raw_token is not None else None

    with SessionLocal() as session:
        logout_user(session, token)

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=LOGIN_COOKIE_NAME,
        path="/",
        secure=settings.login_cookie_secure,
        httponly=True,
        samesite="lax",
    )

    return response
"""登录 HTTP 边界：请求校验、安全错误响应和 Cookie 签发。"""

import logging

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import LOGIN_COOKIE_NAME, settings
from app.database import SessionLocal
from app.schemas import LoginErrorResponse, LoginRequest, LoginResponse
from app.services.authentication_service import InvalidCredentialsError
from app.services.login_session_service import issue_login_session

logger = logging.getLogger(__name__)

def _error_response(
    status_code: int,
    *,
    code: str,
    message: str,
) -> JSONResponse:
    """只输出明确允许公开的错误信息。"""

    payload = LoginErrorResponse(code=code, message=message)

    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
        headers={"Cache-Control": "no-store"},
    )

class LoginRoute(APIRoute):
    """覆盖请求校验、业务执行和响应生成的局部错误边界。"""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        origin_handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                origin = request.headers.get("origin")

                if origin not in settings.login_allowed_origins:
                    return _error_response(
                        status.HTTP_403_FORBIDDEN,
                        code="login_origin_rejected",
                        message="登录请求来源不被允许"
                    )

                content_type = (
                    request.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )

                if content_type != "application/json":
                    return _error_response(
                        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        code="unsupported_login_content_type",
                        message="登录请求必须使用 application/json"
                    )

                response = await origin_handler(request)
                response.headers["Cache-Control"] = "no-store"

                return response
            except RequestValidationError:
                return _error_response(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code="invalid_login_input",
                    message="登录信息不符合要求，请检查用户名和密码",
                )
            except InvalidCredentialsError:
                return _error_response(
                    status.HTTP_401_UNAUTHORIZED,
                    code=InvalidCredentialsError.code,
                    message="用户名或密码错误",
                )
            except StarletteHTTPException as error:
                return _error_response(
                    error.status_code,
                    code="request_rejected",
                    message="请求无法处理",
                )
            except Exception:  # noqa: BLE001 -- HTTP 边界统一脱敏
                logger.error("login_failed")
                return _error_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    code="login_failed",
                    message="登录失败，请稍候再试",
                )
        return safe_handler

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    route_class=LoginRoute,
)


@router.post(
    "/login",
    status_code=status.HTTP_200_OK,
    response_model=LoginResponse,
    responses={
        400: {
            "model": LoginErrorResponse,
            "description": "请求无法解析",
        },
        401: {
            "model": LoginErrorResponse,
            "description": "用户名或密码错误",
        },
        403: {
            "model": LoginErrorResponse,
            "description": "请求来源不被允许",
        },
        415: {
            "model": LoginErrorResponse,
            "description": "请求不是 JSON",
        },
        422: {
            "model": LoginErrorResponse,
            "description": "登录信息校验失败",
        },
        500: {
            "model": LoginErrorResponse,
            "description": "登录失败",
        },
    },
)

def login(payload: LoginRequest, response: Response) -> LoginResponse:
    """服务负责事务，路由负责 Session 生命周期和 Cookie 交付。"""

    with SessionLocal() as session:
        result = issue_login_session(session, payload)

    public_result = LoginResponse(
        external_id=result.user.external_id,
        username=result.user.username
    )

    response.set_cookie(
        key=LOGIN_COOKIE_NAME,
        value=result.token.get_secret_value(),
        expires=result.expires_at,
        path="/",
        secure=settings.login_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return public_result
